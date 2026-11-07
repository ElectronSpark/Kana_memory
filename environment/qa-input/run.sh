#!/system/bin/sh
# The API 36 system launcher omits android.test.base, although its old runner
# references RepetitiveTest. Pass the real runtime base jar as a dependency;
# the system script adds all jars to CLASSPATH and prepares its dex cache.
helper_dir="$(cd "$(dirname "$0")" && pwd)"
helper_jar="$helper_dir/unicode-input.jar"
if [ ! -r "$helper_jar" ]; then
    echo "Missing $helper_jar" >&2
    exit 2
fi
base_jar='/system/framework/android.test.base.jar'
if [ ! -r "$base_jar" ]; then
    echo "Missing system runtime dependency $base_jar" >&2
    exit 2
fi
exec uiautomator runtest "$base_jar" "$helper_jar" -c 'qa.UnicodeInput#testSetText' "$@"
