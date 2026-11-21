KanaMemory app icon

The launcher icon is a vermilion seal with a white kana ka (か). Its third stroke, the short stroke at the upper
right, is drawn as a clockwise arc ending in an arrowhead: the sign for "again", that is, spaced review. The kana
is drawn by hand as three monoline strokes (round caps and joins); it is not taken from a font.

Palette: vermilion #E0452F (field), white #FFFFFF (sign); the mono drawing uses ink #1B1B1F.
Canvas: the Android adaptive-icon grid, 108 units; the sign lies inside the central safe circle of 66 units.

Files
- source/kanamemory_icon.svg       the drawing: vermilion field, white sign (108-unit canvas)
- source/kanamemory_icon_mono.svg  the sign alone in ink on transparent (for themed or notification use)
- kanamemory-square-<n>.png        legacy square icon, a vermilion rounded square on transparent
                                   (48-192 px); the 320-1280 px sizes are the splash image
- kanamemory-round-<n>.png         legacy round icon, a vermilion disc on transparent (48-192 px)
- kanamemory-adaptive-<n>.png      adaptive-icon foreground (108-432 px): a full-bleed vermilion square with
                                   the sign scaled to the launcher's visible 72-unit area, so the icon is
                                   vermilion whatever the adaptive background colour is

app/pyproject.toml names them with icon = "icons/kanamemory"; Briefcase picks
icons/kanamemory-<variant>-<size>.png for each size the Android template asks for.

Regenerate
All files come from tools/render_app_icon.py (Pillow, from the project's own environment). The geometry is in that
script; change it there, then run from the repository root:

    python tools/render_app_icon.py

It rewrites the two drawings and all PNG files here. A generated Android project takes the new files in the
build stage (briefcase update --update-resources) or on a new create.
