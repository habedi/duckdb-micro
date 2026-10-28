/* Path resolution and normalization for interactive shell. */

#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L

#include "path_util.h"

#include <stdio.h>
#include <string.h>

bool path_resolve(const char *cwd, const char *in, char *out, size_t out_len)
{
  if (out_len == 0)
    {
      return false;
    }

  char combined[512];
  int n;
  if (!in || in[0] == '\0')
    {
      n = snprintf(out, out_len, "%s", cwd ? cwd : "/");
      return n >= 0 && (size_t)n < out_len;
    }

  if (in[0] == '/')
    {
      n = snprintf(combined, sizeof(combined), "%s", in);
    }
  else
    {
      n = snprintf(combined, sizeof(combined), "%s/%s", cwd ? cwd : "/", in);
    }
  if (n < 0 || (size_t)n >= sizeof(combined))
    {
      return false;
    }

  /* Segments point into combined, which strtok_r splits in place. */
  char *segments[64];
  int count = 0;

  char *saveptr = NULL;
  char *tok = strtok_r(combined, "/", &saveptr);
  while (tok != NULL)
    {
      if (strcmp(tok, "..") == 0)
        {
          if (count > 0)
            {
              count--;
            }
        }
      else if (strcmp(tok, ".") != 0)
        {
          if (count >= (int)(sizeof(segments) / sizeof(segments[0])))
            {
              return false;
            }
          segments[count++] = tok;
        }
      tok = strtok_r(NULL, "/", &saveptr);
    }

  if (count == 0)
    {
      n = snprintf(out, out_len, "/");
      return n >= 0 && (size_t)n < out_len;
    }

  size_t len = 0;
  out[0] = '\0';
  for (int i = 0; i < count; i++)
    {
      n = snprintf(out + len, out_len - len, "/%s", segments[i]);
      if (n < 0 || (size_t)n >= out_len - len)
        {
          return false;
        }
      len += n;
    }
  return true;
}
