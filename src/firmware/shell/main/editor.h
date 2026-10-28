#ifndef EDITOR_H
#define EDITOR_H

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Launch interactive in-terminal text editor on the specified file path.
 * Supports VT100/ANSI terminal sequences (arrows, backspace, insert, delete).
 * Shortcuts:
 *   Ctrl+S: Save file
 *   Ctrl+Q: Exit editor
 *   Ctrl+X: Exit editor
 *
 * @param filepath Full path to file (e.g. /sdcard/notes.txt)
 * @return 0 on normal exit, non-zero on error.
 */
int editor_open(const char *filepath);

#ifdef __cplusplus
}
#endif

#endif /* EDITOR_H */
