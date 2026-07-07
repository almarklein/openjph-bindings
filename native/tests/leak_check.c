/* Leak and error-path exercise for the public C API.
 *
 * Build with -fsanitize=address and run with ASAN_OPTIONS=detect_leaks=1:
 * LeakSanitizer reports any allocation that never crossed back over
 * openjph_free at process exit, which makes ownership bugs deterministic
 * instead of statistical. Compiling this file as plain C also verifies that
 * openjph_c.h remains a valid C header.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "openjph_c.h"

#define CHECK(cond)                                                            \
  do {                                                                         \
    if (!(cond)) {                                                             \
      fprintf(stderr, "FAILED at %s:%d: %s\n", __FILE__, __LINE__, #cond);     \
      exit(1);                                                                 \
    }                                                                          \
  } while (0)

static openjph_encode_params_t default_params(void) {
  openjph_encode_params_t p;
  memset(&p, 0, sizeof(p));
  p.irreversible = 0;
  p.qstep = 0.0f;
  p.use_qstep = 0;
  p.num_decompositions = 5;
  p.block_width = 64;
  p.block_height = 64;
  memcpy(p.progression_order, "LRCP", 5);
  p.color_transform = 0;
  p.planar = 1;
  return p;
}

static openjph_array_t make_array(const uint16_t *data, size_t ndim, size_t d0,
                                  size_t d1, size_t d2) {
  openjph_array_t a;
  memset(&a, 0, sizeof(a));
  a.data = data;
  a.ndim = ndim;
  a.dims[0] = d0;
  a.dims[1] = d1;
  a.dims[2] = d2;
  a.bit_depth = 16;
  a.is_signed = 0;
  return a;
}

static uint16_t *make_gradient(size_t n_elems) {
  uint16_t *data = (uint16_t *)malloc(n_elems * sizeof(uint16_t));
  CHECK(data != NULL);
  for (size_t i = 0; i < n_elems; ++i)
    data[i] = (uint16_t)(i % 60000u);
  return data;
}

/* Encode img, decode the result, verify shape and a sample of pixel values,
   free both C-allocated buffers. */
static void roundtrip(const openjph_array_t *img) {
  openjph_encode_params_t params = default_params();
  char err[1024];

  uint8_t *cs = NULL;
  size_t cs_len = 0;
  CHECK(openjph_encode(img, &params, &cs, &cs_len, err, sizeof(err)) == 0);
  CHECK(cs != NULL && cs_len > 0);

  uint8_t *out = NULL;
  size_t out_len = 0, out_ndim = 0, out_dims[3] = {0, 0, 0};
  uint32_t bd = 0;
  int32_t sgn = 0;
  CHECK(openjph_decode(cs, cs_len, &out, &out_len, &out_ndim, out_dims, &bd,
                       &sgn, err, sizeof(err)) == 0);
  CHECK(out != NULL);
  CHECK(bd == 16 && sgn == 0);

  size_t comp = (img->ndim == 3) ? img->dims[0] : 1;
  size_t h = (img->ndim == 3) ? img->dims[1] : img->dims[0];
  size_t w = (img->ndim == 3) ? img->dims[2] : img->dims[1];
  size_t total = comp * h * w;
  CHECK(out_len == total * sizeof(uint16_t));
  /* A 1-component codestream decodes as 2-D: the SIZ marker cannot express a
     leading singleton axis. */
  if (comp == 1) {
    CHECK(out_ndim == 2 && out_dims[0] == h && out_dims[1] == w);
  } else {
    CHECK(out_ndim == 3 && out_dims[0] == comp && out_dims[1] == h &&
          out_dims[2] == w);
  }

  const uint16_t *src = (const uint16_t *)img->data;
  const uint16_t *dst = (const uint16_t *)(void *)out;
  size_t step = total / 97 + 1; /* sample ~97 pixels across the image */
  for (size_t i = 0; i < total; i += step)
    CHECK(dst[i] == src[i]);
  CHECK(dst[total - 1] == src[total - 1]);

  openjph_free(out);
  openjph_free(cs);
}

static void happy_paths(void) {
  static const size_t shapes[][4] = {
      /* ndim, d0, d1, d2 */
      {2, 64, 96, 0}, /* 2-D */
      {3, 3, 32, 48}, /* 3-D multi-component */
      {3, 1, 32, 48}, /* 3-D singleton component */
  };
  for (size_t s = 0; s < sizeof(shapes) / sizeof(shapes[0]); ++s) {
    size_t ndim = shapes[s][0];
    size_t d0 = shapes[s][1], d1 = shapes[s][2], d2 = shapes[s][3];
    size_t total = (ndim == 2) ? d0 * d1 : d0 * d1 * d2;
    uint16_t *data = make_gradient(total);
    openjph_array_t img = make_array(data, ndim, d0, d1, d2);
    for (int i = 0; i < 50; ++i)
      roundtrip(&img);
    free(data);
  }
}

static void encode_error_paths(void) {
  uint16_t *data = make_gradient(64 * 96);
  char err[1024];
  uint8_t *out = NULL;
  size_t out_len = 0;

  /* Each case must return -1 without setting the output pointer (nothing for
     the caller to free). */

  { /* invalid ndim */
    openjph_array_t img = make_array(data, 5, 64, 96, 0);
    openjph_encode_params_t p = default_params();
    CHECK(openjph_encode(&img, &p, &out, &out_len, err, sizeof(err)) == -1);
    CHECK(out == NULL);
  }
  { /* bit_depth 0 */
    openjph_array_t img = make_array(data, 2, 64, 96, 0);
    img.bit_depth = 0;
    openjph_encode_params_t p = default_params();
    CHECK(openjph_encode(&img, &p, &out, &out_len, err, sizeof(err)) == -1);
    CHECK(out == NULL);
  }
  { /* qstep without irreversible */
    openjph_array_t img = make_array(data, 2, 64, 96, 0);
    openjph_encode_params_t p = default_params();
    p.use_qstep = 1;
    p.qstep = 0.01f;
    CHECK(openjph_encode(&img, &p, &out, &out_len, err, sizeof(err)) == -1);
    CHECK(out == NULL);
  }
  { /* color_transform with 2 components */
    openjph_array_t img = make_array(data, 3, 2, 64, 48);
    openjph_encode_params_t p = default_params();
    p.color_transform = 1;
    p.planar = 0;
    CHECK(openjph_encode(&img, &p, &out, &out_len, err, sizeof(err)) == -1);
    CHECK(out == NULL);
  }
  { /* zero dimension */
    openjph_array_t img = make_array(data, 2, 0, 96, 0);
    openjph_encode_params_t p = default_params();
    CHECK(openjph_encode(&img, &p, &out, &out_len, err, sizeof(err)) == -1);
    CHECK(out == NULL);
  }

  free(data);
}

static void decode_error_paths(void) {
  char err[1024];

  /* A valid codestream to truncate and corrupt. */
  uint16_t *data = make_gradient(64 * 96);
  openjph_array_t img = make_array(data, 2, 64, 96, 0);
  openjph_encode_params_t params = default_params();
  uint8_t *cs = NULL;
  size_t cs_len = 0;
  CHECK(openjph_encode(&img, &params, &cs, &cs_len, err, sizeof(err)) == 0);
  CHECK(cs_len > 64);
  free(data);

  uint8_t *out = NULL;
  size_t out_len = 0, out_ndim = 0, out_dims[3] = {0, 0, 0};
  uint32_t bd = 0;
  int32_t sgn = 0;

  { /* empty stream */
    uint8_t dummy = 0;
    CHECK(openjph_decode(&dummy, 0, &out, &out_len, &out_ndim, out_dims, &bd,
                         &sgn, err, sizeof(err)) == -1);
    CHECK(out == NULL);
  }
  { /* garbage bytes */
    uint8_t garbage[64];
    memset(garbage, 0xAB, sizeof(garbage));
    CHECK(openjph_decode(garbage, sizeof(garbage), &out, &out_len, &out_ndim,
                         out_dims, &bd, &sgn, err, sizeof(err)) == -1);
    CHECK(out == NULL);
  }
  { /* truncated header: first 10 bytes of a valid stream */
    CHECK(openjph_decode(cs, 10, &out, &out_len, &out_ndim, out_dims, &bd, &sgn,
                         err, sizeof(err)) == -1);
    CHECK(out == NULL);
  }
  { /* truncated body: headers parse, decode starves mid-stream. HTJ2K
       tolerates mid-stream truncation by design, so the decode may succeed
       (partial image) or fail — either way nothing must leak. */
    int ret = openjph_decode(cs, cs_len / 2, &out, &out_len, &out_ndim,
                             out_dims, &bd, &sgn, err, sizeof(err));
    if (ret == 0) {
      CHECK(out != NULL);
      openjph_free(out);
      out = NULL;
    } else {
      CHECK(ret == -1 && out == NULL);
    }
  }
  { /* corrupt tail */
    uint8_t *bad = (uint8_t *)malloc(cs_len);
    CHECK(bad != NULL);
    memcpy(bad, cs, cs_len);
    memset(bad + cs_len - 16, 0xFF, 16);
    /* Bit corruption in the entropy-coded tail may or may not be detected —
       either way nothing must leak and any success must hand out a buffer. */
    int ret = openjph_decode(bad, cs_len, &out, &out_len, &out_ndim, out_dims,
                             &bd, &sgn, err, sizeof(err));
    if (ret == 0) {
      CHECK(out != NULL);
      openjph_free(out);
      out = NULL;
    } else {
      CHECK(ret == -1 && out == NULL);
    }
    free(bad);
  }
  { /* err_buf truncation: a 4-byte err_buf must be safely NUL-terminated */
    char tiny[4];
    memset(tiny, 0x7F, sizeof(tiny));
    uint8_t garbage[32];
    memset(garbage, 0xCD, sizeof(garbage));
    CHECK(openjph_decode(garbage, sizeof(garbage), &out, &out_len, &out_ndim,
                         out_dims, &bd, &sgn, tiny, sizeof(tiny)) == -1);
    CHECK(tiny[3] == '\0');
    CHECK(out == NULL);
  }

  openjph_free(cs);
}

int main(void) {
  happy_paths();
  encode_error_paths();
  decode_error_paths();
  printf("leak_check: all checks passed (LeakSanitizer verdict at exit)\n");
  return 0;
}
