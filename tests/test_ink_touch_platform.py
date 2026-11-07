"""Batch touch bridge contracts; the Java state machine is exercised on a JVM."""
import gc
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from types import SimpleNamespace
import weakref

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.platform_android import AndroidPlatform


class NativeView:
    def setOnTouchListener(self, listener):
        self.listener = listener


class Listener:
    def __init__(self, density, events):
        self.density, self.events = density, events
        self.detached = False

    def detach(self, native):
        self.detached = True
        native.setOnTouchListener(None)


class Canvas:
    def __init__(self):
        self._impl = SimpleNamespace(dpi_scale=2.5, native=NativeView())


@pytest.fixture
def platform():
    service = AndroidPlatform.__new__(AndroidPlatform)
    service._closed = False
    service._jclass = {"org.example.kanamemory.KanaInkTouchListener": Listener,
        "org.example.kanamemory.KanaInkTouchListener$Events": object}.__getitem__
    service._dynamic_proxy = lambda interface: object
    return service


def test_batch_preserves_subpixel_coordinates_historical_timing_and_single_callback(platform):
    canvas, calls = Canvas(), []
    assert platform.bind_ink_touch(canvas, lambda phase, points: calls.append((phase, points)))
    native = canvas._impl.native.listener
    assert native.density == 2.5
    samples = [(i + .125, i + .375, 10000000000 + i * 7) for i in range(1000)]
    native.events.onSamples(1, b''.join(struct.pack('<ffq', *sample) for sample in samples))
    assert calls == [('move', samples)]
    native.events.onSamples(3, b'')
    assert calls[-1] == ('cancel', [])


def test_rebinding_makes_old_callbacks_inert_and_detaches_listener(platform):
    canvas, calls = Canvas(), []
    platform.bind_ink_touch(canvas, lambda *value: calls.append(value))
    old = canvas._impl.native.listener
    platform.bind_ink_touch(canvas, lambda *value: calls.append(('new', *value)))
    old.events.onSamples(3, b'')
    assert old.detached and not calls
    canvas._impl.native.listener.events.onSamples(3, b'')
    assert calls == [('new', 'cancel', [])]


def test_java_proxy_does_not_retain_page_and_closed_platform_is_inert(platform):
    class Owner:
        def __init__(self):
            self.canvas = Canvas()
        def receive(self, *args):
            raise AssertionError('An obsolete page must not be called')
    owner = Owner()
    platform.bind_ink_touch(owner.canvas, owner.receive)
    native = owner.canvas._impl.native.listener
    owner_ref, canvas_ref = weakref.ref(owner), weakref.ref(owner.canvas)
    del owner
    gc.collect()
    assert owner_ref() is None and canvas_ref() is None
    native.events.onSamples(3, b'')
    canvas = Canvas()
    platform.bind_ink_touch(canvas, lambda *args: pytest.fail('Closed platform'))
    platform._closed = True
    canvas._impl.native.listener.events.onSamples(3, b'')


def test_invalid_binding_does_not_replace_current_listener(platform):
    canvas = Canvas()
    platform.bind_ink_touch(canvas, lambda *args: None)
    old = canvas._impl.native.listener
    with pytest.raises(TypeError):
        platform.bind_ink_touch(canvas, None)
    for density in (0, -1, float('nan'), float('inf')):
        canvas._impl.dpi_scale = density
        with pytest.raises(ValueError):
            platform.bind_ink_touch(canvas, lambda *args: None)
    assert canvas._impl.native.listener is old and not old.detached


def test_native_motion_events_preserve_history_pointer_identity_and_cancellation(tmp_path):
    bundled = Path(os.environ.get('LOCALAPPDATA', '')) / 'Temp/km/briefcase/tools/java17/bin'
    java_bin = Path(os.environ.get('KANAMEMORY_TEST_JAVA_BIN', str(bundled)))
    javac = str(java_bin / 'javac.exe') if (java_bin / 'javac.exe').is_file() else shutil.which('javac')
    java = str(java_bin / 'java.exe') if (java_bin / 'java.exe').is_file() else shutil.which('java')
    if not javac or not java:
        pytest.skip('JDK required for actual Java touch state machine')
    if any('javapath' in Path(command).parts for command in (javac, java)):
        pytest.skip('Use KANAMEMORY_TEST_JAVA_BIN for a real JDK, not the Oracle launcher shim')
    sources = {
        'android/view/ViewParent.java': '''package android.view;
            public interface ViewParent { void requestDisallowInterceptTouchEvent(boolean value); }''',
        'android/view/View.java': '''package android.view;
            public class View {
                public interface OnTouchListener { boolean onTouch(View v, MotionEvent e); }
                public boolean locked; public int width=200,height=200;
                private ViewParent parent = value -> locked=value;
                public ViewParent getParent(){return parent;}
                public int getWidth(){return width;} public int getHeight(){return height;}
                public void setOnTouchListener(OnTouchListener l){}
            }''',
        'android/view/MotionEvent.java': '''package android.view;
            public class MotionEvent {
                public static final int ACTION_DOWN=0,ACTION_UP=1,ACTION_MOVE=2,ACTION_CANCEL=3,
                    ACTION_POINTER_DOWN=5,ACTION_POINTER_UP=6;
                public int action,index; public int[] ids; public float[][] xy;
                public long[] times;
                public MotionEvent(int a,int ix,int[] ids,float[][] points,long[] t){
                    action=a;index=ix;this.ids=ids;xy=points;times=t;}
                public int getActionMasked(){return action;} public int getActionIndex(){return index;}
                public int getPointerId(int i){return ids[i];}
                public int findPointerIndex(int id){for(int i=0;i<ids.length;i++)if(ids[i]==id)return i;return -1;}
                public int getHistorySize(){return xy.length-1;}
                public float getX(int i){return xy[xy.length-1][i*2];}
                public float getY(int i){return xy[xy.length-1][i*2+1];}
                public float getHistoricalX(int i,int h){return xy[h][i*2];}
                public float getHistoricalY(int i,int h){return xy[h][i*2+1];}
                public long getEventTime(){return times[times.length-1];}
                public long getHistoricalEventTime(int h){return times[h];}
            }''',
        'InkTouchHarness.java': '''import android.view.*; import java.nio.*; import java.util.*;
            import org.example.kanamemory.KanaInkTouchListener;
            public class InkTouchHarness {
                static ArrayList<Integer> phases=new ArrayList<>();
                static ArrayList<byte[]> packets=new ArrayList<>();
                static void check(boolean yes){if(!yes)throw new AssertionError();}
                static MotionEvent e(int action,int index,int[] ids,float[][] xy,long... t){
                    return new MotionEvent(action,index,ids,xy,t);}
                static MotionEvent p(int action,float x,float y){
                    return e(action,0,new int[]{4},new float[][]{{x,y}},1000);}
                static ByteBuffer last(){return ByteBuffer.wrap(packets.get(packets.size()-1)).order(ByteOrder.LITTLE_ENDIAN);}
                public static void main(String[] args){
                    View view=new View();
                    KanaInkTouchListener ink=new KanaInkTouchListener(2,(phase,data)->{phases.add(phase);packets.add(data);});
                    check(ink.onTouch(view,p(0,10.5f,12.25f))&&view.locked);
                    ByteBuffer down=last(); check(down.getFloat()==5.25f&&down.getFloat()==6.125f&&down.getLong()==1000);
                    ink.onTouch(view,e(5,0,new int[]{9,4},new float[][]{{180,180,10,12}},1001));
                    check(phases.size()==1);
                    ink.onTouch(view,e(2,0,new int[]{9,4},new float[][]{
                        {180,180,12,14},{180,180,14,16},{180,180,16,18},{180,180,18,20}},1001,1002,1003,1004));
                    check(phases.size()==2&&phases.get(1)==1&&last().remaining()==64);
                    ByteBuffer move=last();for(int i=0;i<4;i++)check(move.getFloat()==6+i&&move.getFloat()==7+i&&move.getLong()==1001+i);
                    ink.onTouch(view,e(6,1,new int[]{9,4},new float[][]{{180,180,20,22}},1005));
                    check(phases.get(2)==2&&view.locked);
                    ink.onTouch(view,e(2,0,new int[]{9},new float[][]{{100,100}},1006));
                    ink.onTouch(view,e(1,0,new int[]{9},new float[][]{{100,100}},1007));
                    check(phases.size()==3&&!view.locked);
                    ink.onTouch(view,p(0,10,10));ink.onTouch(view,p(3,10,10));
                    check(phases.get(phases.size()-1)==3&&last().remaining()==0&&!view.locked);
                    ink.onTouch(view,p(0,10,10));
                    ink.onTouch(view,e(2,0,new int[]{4},new float[][]{{15,15},{210,20},{30,30}},1010,1011,1012));
                    check(phases.get(phases.size()-1)==3&&last().remaining()==16&&view.locked);
                    int count=phases.size();ink.onTouch(view,p(2,25,25));ink.onTouch(view,p(1,25,25));
                    check(phases.size()==count&&!view.locked);
                    check(!ink.onTouch(view,p(0,-1,5))&&!view.locked);
                    ink.onTouch(view,p(0,10,10));
                    ink.onTouch(view,e(2,0,new int[]{9},new float[][]{{20,20}},1020));
                    check(phases.get(phases.size()-1)==3);
                    ink.onTouch(view,p(1,20,20));check(!view.locked);
                    ink.onTouch(view,p(0,10,10));
                    float[][] points=new float[1000][2];long[] times=new long[1000];
                    for(int i=0;i<1000;i++){points[i][0]=10+i%100+.25f;points[i][1]=30;times[i]=10000000000L+i;}
                    ink.onTouch(view,e(2,0,new int[]{4},points,times));
                    check(last().remaining()==16000); ByteBuffer batch=last();
                    for(int i=0;i<1000;i++)check(batch.getFloat()==(10+i%100+.25f)/2&&batch.getFloat()==15&&batch.getLong()==times[i]);
                    count=phases.size();ink.detach(view);check(!view.locked);
                    check(!ink.onTouch(view,p(2,20,20))&&phases.size()==count);
                    System.out.println("historical samples, pointer ownership, bounds, cancel, scroll and detach passed");
                }
            }''',
    }
    for name, text in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    target = tmp_path / 'org/example/kanamemory/KanaInkTouchListener.java'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / 'app/native/java/org/example/kanamemory/KanaInkTouchListener.java', target)
    compiled = subprocess.run([javac, '-encoding', 'UTF-8', '-d', 'classes', *sources,
        'org/example/kanamemory/KanaInkTouchListener.java'], cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    checked = subprocess.run([java, '-cp', 'classes', 'InkTouchHarness'], cwd=tmp_path,
                             text=True, capture_output=True, timeout=30)
    assert checked.returncode == 0, checked.stdout + checked.stderr
