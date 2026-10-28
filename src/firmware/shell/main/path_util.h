/* Path resolution and normalization for interactive shell. */

#ifndef PATH_UTIL_H
#define PATH_UTIL_H

#include <stdbool.h>
#include <stddef.h>

/* Resolves in against cwd, normalizing '.' and '..' segments.
 * Returns false when the normalized path cannot fit into out.
 */
bool path_resolve(const char *cwd, const char *in, char *out, size_t out_len);

#endif /* PATH_UTIL_H */
