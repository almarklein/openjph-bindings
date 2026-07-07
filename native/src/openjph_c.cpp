#include <algorithm>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "ojph_base.h"
#include "ojph_codestream.h"
#include "ojph_file.h"
#include "ojph_mem.h"
#include "ojph_message.h"
#include "ojph_params.h"

#include "openjph_c.h"

namespace {

/* OpenJPH's default error handler prints the detailed diagnostic (message,
   file, line) to stderr and then throws a generic
   std::runtime_error("ojph error") — so the useful text never reached the
   caller's err_buf. This handler formats the diagnostic into the thrown
   exception instead: nothing prints to the console, and the catch blocks
   below copy the real message into err_buf via e.what(). Thread-safe: the
   message lives in the exception object, no shared buffer. */
class CapturingError final : public ojph::message_error {
public:
  void operator()(int error_code, const char *file_name, int line_num,
                  const char *fmt, ...) override {
    char msg[512];
    va_list args;
    va_start(args, fmt);
    std::vsnprintf(msg, sizeof(msg), fmt, args);
    va_end(args);
    char full[640];
    std::snprintf(full, sizeof(full), "%s (%s:%d, code 0x%08X)", msg, file_name,
                  line_num, static_cast<unsigned int>(error_code));
    throw std::runtime_error(full);
  }
};

/* Install the capturing error handler and silence the info/warning console
   streams. This must run lazily (on first API call) rather than from a
   namespace-scope constructor: OpenJPH's message globals are themselves
   dynamically initialized (error_stream = stderr, local_error = &error in
   ojph_message.cpp), and cross-TU initialization order would let those
   overwrite an early setup — observably, a load-time silencer still left
   every decode error printing to stderr. */
void configure_ojph_messages() {
  static const bool done = [] {
    ojph::set_info_stream(nullptr);
    ojph::set_warning_stream(nullptr);
    static CapturingError handler;
    ojph::configure_error(&handler);
    return true;
  }();
  (void)done;
}

struct ArrayInfo {
  uint32_t bit_depth;
  bool is_signed;
  size_t ndim;
  size_t components;
  size_t height;
  size_t width;
};

size_t row_offset(const ArrayInfo &info, size_t component, size_t row) {
  if (info.ndim == 2)
    return row * info.width;
  return (component * info.height + row) * info.width;
}

uint32_t max_decompositions(size_t width, size_t height) {
  size_t v = std::min(width, height);
  uint32_t levels = 0;
  while (v >= 2) {
    ++levels;
    v >>= 1;
  }
  return levels;
}

/* Portable overflow-checked multiply (no compiler builtins, so MSVC is fine).
   Throws if a*b would wrap size_t. */
size_t checked_mul(size_t a, size_t b) {
  if (a != 0 && b > std::numeric_limits<size_t>::max() / a)
    throw std::runtime_error("Decoded image dimensions overflow");
  return a * b;
}

/* Copy a row from a typed source pointer into an OpenJPH line buffer.
   Handles all three internal buffer formats (float, si32, si64). */
template <typename Src>
void copy_to_linebuf_impl(const Src *src, size_t width, ojph::line_buf *line) {
  if ((line->flags & ojph::line_buf::LFT_32BIT) &&
      (line->flags & ojph::line_buf::LFT_INTEGER) == 0) {
    for (size_t i = 0; i < width; ++i)
      line->f32[i] = static_cast<float>(src[i]);
    return;
  }
  if (line->flags & ojph::line_buf::LFT_32BIT) {
    for (size_t i = 0; i < width; ++i)
      line->i32[i] = static_cast<ojph::si32>(src[i]);
    return;
  }
  if (line->flags & ojph::line_buf::LFT_64BIT) {
    for (size_t i = 0; i < width; ++i)
      line->i64[i] = static_cast<ojph::si64>(src[i]);
    return;
  }
  throw std::runtime_error("Unsupported OpenJPH line buffer format");
}

template <typename Dst, typename Src> inline Dst clamp_cast(Src v) {
  // When Src is a signed integer and Dst is unsigned with the same width
  // (e.g. si32 → uint32_t), static_cast<Src>(Dst::max()) would overflow.
  // In lossless mode the encoder stored values with static_cast<Src>, so
  // the correct reverse is also static_cast<Dst> (bit reinterpretation).
  if constexpr (std::is_integral_v<Src> && std::is_integral_v<Dst> &&
                std::is_signed_v<Src> && std::is_unsigned_v<Dst> &&
                sizeof(Dst) >= sizeof(Src)) {
    return static_cast<Dst>(v);
  } else {
    constexpr Src lo = static_cast<Src>(std::numeric_limits<Dst>::min());
    constexpr Src hi = static_cast<Src>(std::numeric_limits<Dst>::max());
    if (v < lo)
      return std::numeric_limits<Dst>::min();
    if (v > hi)
      return std::numeric_limits<Dst>::max();
    return static_cast<Dst>(v);
  }
}

template <typename Dst>
void copy_from_linebuf_impl(const ojph::line_buf *line, Dst *dst,
                            size_t width) {
  if ((line->flags & ojph::line_buf::LFT_32BIT) &&
      (line->flags & ojph::line_buf::LFT_INTEGER) == 0) {
    for (size_t i = 0; i < width; ++i)
      dst[i] = clamp_cast<Dst>(line->f32[i]);
    return;
  }
  if (line->flags & ojph::line_buf::LFT_32BIT) {
    for (size_t i = 0; i < width; ++i)
      dst[i] = clamp_cast<Dst>(line->i32[i]);
    return;
  }
  if (line->flags & ojph::line_buf::LFT_64BIT) {
    for (size_t i = 0; i < width; ++i)
      dst[i] = clamp_cast<Dst>(line->i64[i]);
    return;
  }
  throw std::runtime_error("Unsupported OpenJPH line buffer format");
}

template <typename Dst>
void copy_linebuf_to_array(const ojph::line_buf *line, Dst *data,
                           const ArrayInfo &info, size_t component,
                           size_t row) {
  Dst *dst = data + row_offset(info, component, row);
  copy_from_linebuf_impl(line, dst, info.width);
}

template <typename T>
T *decode_to_buffer(ojph::codestream &codestream, const ArrayInfo &info) {
  const size_t total = info.components * info.height * info.width;
  const size_t total_lines = info.components * info.height;
  // RAII: the buffer is freed on any throw below (e.g. a short/corrupt stream
  // where pull() returns null mid-loop), and only released to the caller on
  // success. Caller frees it via openjph_free (== std::free).
  std::unique_ptr<T, decltype(&std::free)> data(
      static_cast<T *>(std::malloc(total * sizeof(T))), &std::free);
  if (!data)
    throw std::runtime_error("Failed to allocate decode buffer");
  std::fill(data.get(), data.get() + total, T{});

  std::vector<size_t> rows(info.components, 0);
  for (size_t line_index = 0; line_index < total_lines; ++line_index) {
    ojph::ui32 component = 0;
    ojph::line_buf *line = codestream.pull(component);
    if (line == nullptr)
      throw std::runtime_error(
          "OpenJPH decode ended before the requested array was filled");
    const size_t row = rows[component]++;
    copy_linebuf_to_array(line, data.get(), info, component, row);
  }
  return data.release();
}

/* Copy one row of the input C buffer into an ojph::line_buf.
   Dispatches on bit_depth and is_signed to select the correct pointer type.
   The line_buf handed back by exchange()/pull() is always si32
   (LFT_32BIT | LFT_INTEGER) for every bit depth and both reversible and
   irreversible modes; the si64/float branches in the copy helpers are dead for
   this API path. (32-bit irreversible is lossy and may lose precision.) */
void copy_row_to_linebuf_c(const void *data, const ArrayInfo &info,
                           size_t component, size_t row, ojph::line_buf *line) {
  const size_t off = row_offset(info, component, row);
  const uint32_t bd = info.bit_depth;
  const bool sgn = info.is_signed;

  if (bd <= 8 && !sgn)
    copy_to_linebuf_impl(static_cast<const uint8_t *>(data) + off, info.width,
                         line);
  else if (bd <= 8)
    copy_to_linebuf_impl(static_cast<const int8_t *>(data) + off, info.width,
                         line);
  else if (bd <= 16 && !sgn)
    copy_to_linebuf_impl(static_cast<const uint16_t *>(data) + off, info.width,
                         line);
  else if (bd <= 16)
    copy_to_linebuf_impl(static_cast<const int16_t *>(data) + off, info.width,
                         line);
  else if (!sgn)
    copy_to_linebuf_impl(static_cast<const uint32_t *>(data) + off, info.width,
                         line);
  else
    copy_to_linebuf_impl(static_cast<const int32_t *>(data) + off, info.width,
                         line);
}

int encode_impl_c(const openjph_array_t *img,
                  const openjph_encode_params_t *params, uint8_t **out,
                  size_t *out_len, char *err_buf, size_t err_buf_len) {
  try {
    configure_ojph_messages();
    if (img->ndim != 2 && img->ndim != 3)
      throw std::runtime_error("ndim must be 2 or 3");
    if (img->bit_depth == 0 || img->bit_depth > 32)
      throw std::runtime_error("bit_depth must be in [1, 32]");
    if (params->block_width <= 0 || params->block_height <= 0)
      throw std::runtime_error("block dimensions must be positive");
    if (!params->irreversible && params->use_qstep)
      throw std::runtime_error("qstep is only valid when irreversible=1");

    ArrayInfo info;
    info.bit_depth = img->bit_depth;
    info.is_signed = (img->is_signed != 0);
    info.ndim = img->ndim;
    if (img->ndim == 2) {
      info.components = 1;
      info.height = img->dims[0];
      info.width = img->dims[1];
    } else {
      info.components = img->dims[0];
      info.height = img->dims[1];
      info.width = img->dims[2];
    }

    if (info.components == 0 || info.height == 0 || info.width == 0)
      throw std::runtime_error("image dimensions must be non-zero");

    if (params->color_transform && info.components != 3)
      throw std::runtime_error("color_transform requires exactly 3 components");

    ojph::mem_outfile outfile;
    outfile.open();

    {
      ojph::codestream codestream;
      codestream.set_planar(params->planar != 0);

      auto siz = codestream.access_siz();
      siz.set_image_extent(ojph::point(static_cast<ojph::ui32>(info.width),
                                       static_cast<ojph::ui32>(info.height)));
      siz.set_image_offset(ojph::point(0, 0));
      siz.set_tile_size(ojph::size(static_cast<ojph::ui32>(info.width),
                                   static_cast<ojph::ui32>(info.height)));
      siz.set_tile_offset(ojph::point(0, 0));
      siz.set_num_components(static_cast<ojph::ui32>(info.components));

      for (size_t c = 0; c < info.components; ++c) {
        siz.set_component(static_cast<ojph::ui32>(c), ojph::point(1, 1),
                          info.bit_depth, info.is_signed);
      }

      auto cod = codestream.access_cod();
      uint32_t clamped =
          std::min<uint32_t>(static_cast<uint32_t>(params->num_decompositions),
                             max_decompositions(info.width, info.height));
      cod.set_num_decomposition(clamped);
      cod.set_block_dims(static_cast<ojph::ui32>(params->block_width),
                         static_cast<ojph::ui32>(params->block_height));
      // set_progression_order() strlen's its argument, but progression_order is
      // a fixed char[8] that callers may fill completely (no terminator). Copy
      // into a NUL-terminated local to avoid an out-of-bounds read.
      char po[9];
      std::memcpy(po, params->progression_order, 8);
      po[8] = '\0';
      cod.set_progression_order(po);
      cod.set_color_transform(params->color_transform != 0);
      cod.set_reversible(params->irreversible == 0);
      if (params->irreversible && params->use_qstep) {
        auto qcd = codestream.access_qcd();
        qcd.set_irrev_quant(params->qstep);
      }

      codestream.write_headers(&outfile);

      std::vector<size_t> rows(info.components, 0);
      ojph::ui32 component = 0;
      ojph::line_buf *line = codestream.exchange(nullptr, component);
      while (line != nullptr) {
        const size_t row = rows[component]++;
        copy_row_to_linebuf_c(img->data, info, component, row, line);
        line = codestream.exchange(line, component);
      }

      codestream.flush();
      codestream.close();
    }

    const size_t n = outfile.get_used_size();
    uint8_t *buf = static_cast<uint8_t *>(std::malloc(n));
    if (!buf)
      throw std::runtime_error("Failed to allocate encode output buffer");
    std::memcpy(buf, outfile.get_data(), n);
    *out = buf;
    *out_len = n;
    return 0;

  } catch (const std::exception &e) {
    std::snprintf(err_buf, err_buf_len, "%s", e.what());
    return -1;
  }
}

int decode_impl_c(const uint8_t *codestream_data, size_t codestream_len,
                  uint8_t **out, size_t *out_len, size_t *out_ndim,
                  size_t out_dims[3], uint32_t *out_bit_depth,
                  int32_t *out_is_signed, char *err_buf, size_t err_buf_len) {
  try {
    configure_ojph_messages();
    ojph::mem_infile infile;
    infile.open(reinterpret_cast<const ojph::ui8 *>(codestream_data),
                codestream_len);

    ojph::codestream codestream;
    // Decode planarity is dictated by the codestream's color-transform flag and
    // is set inside read_headers(); an explicit set_planar() here is
    // overwritten.
    codestream.read_headers(&infile);

    /* Read shape and element type from the codestream SIZ marker. */
    auto siz = codestream.access_siz();
    const uint32_t comp = siz.get_num_components();
    const uint32_t w = siz.get_image_extent().x;
    const uint32_t h = siz.get_image_extent().y;
    const uint32_t bd = siz.get_bit_depth(0);
    const bool sgn = siz.is_signed(0);

    /* The (components x height x width x bytes) product below is computed and
       malloc'd by this wrapper, so an unchecked overflow would under-allocate.
       OpenJPH validates individual SIZ fields but does not bound total image
       area, so reject zero/overflowing/absurd dimensions from untrusted input.
     */
    if (comp == 0 || w == 0 || h == 0)
      throw std::runtime_error("Decoded image has a zero dimension");
    const size_t bytes_per_sample = (bd <= 8) ? 1u : (bd <= 16) ? 2u : 4u;
    const size_t total_bytes =
        checked_mul(checked_mul(checked_mul(comp, h), w), bytes_per_sample);
    constexpr size_t kMaxDecodeBytes = size_t(1) << 33; // 8 GiB sanity cap
    if (total_bytes > kMaxDecodeBytes)
      throw std::runtime_error("Decoded image exceeds the size limit");

    *out_bit_depth = bd;
    *out_is_signed = sgn ? 1 : 0;

    ArrayInfo info;
    info.bit_depth = bd;
    info.is_signed = sgn;
    info.ndim = (comp == 1) ? 2 : 3;
    info.components = comp;
    info.height = h;
    info.width = w;

    codestream.create();

    void *decoded = nullptr;
    if (bd <= 8 && !sgn)
      decoded = decode_to_buffer<uint8_t>(codestream, info);
    else if (bd <= 8)
      decoded = decode_to_buffer<int8_t>(codestream, info);
    else if (bd <= 16 && !sgn)
      decoded = decode_to_buffer<uint16_t>(codestream, info);
    else if (bd <= 16)
      decoded = decode_to_buffer<int16_t>(codestream, info);
    else if (!sgn)
      decoded = decode_to_buffer<uint32_t>(codestream, info);
    else
      decoded = decode_to_buffer<int32_t>(codestream, info);

    // Guard the C-allocated buffer across codestream.close(): if close()
    // throws, the buffer is freed; on success ownership is released to the
    // caller (who frees it via openjph_free).
    std::unique_ptr<void, decltype(&std::free)> decoded_guard(decoded,
                                                              &std::free);

    codestream.close();

    // bytes_per_sample and the element total were validated against overflow
    // above.
    const size_t total = info.components * info.height * info.width;

    *out = static_cast<uint8_t *>(decoded_guard.release());
    *out_len = total * bytes_per_sample;

    *out_ndim = info.ndim;
    if (info.ndim == 2) {
      out_dims[0] = info.height;
      out_dims[1] = info.width;
      out_dims[2] = 0;
    } else {
      out_dims[0] = info.components;
      out_dims[1] = info.height;
      out_dims[2] = info.width;
    }

    return 0;

  } catch (const std::exception &e) {
    std::snprintf(err_buf, err_buf_len, "%s", e.what());
    return -1;
  }
}

} // namespace

/* ---- Exported C API ---- */

extern "C" {

int openjph_encode(const openjph_array_t *img,
                   const openjph_encode_params_t *params, uint8_t **out,
                   size_t *out_len, char *err_buf, size_t err_buf_len) {
  return encode_impl_c(img, params, out, out_len, err_buf, err_buf_len);
}

int openjph_decode(const uint8_t *codestream, size_t codestream_len,
                   uint8_t **out, size_t *out_len, size_t *out_ndim,
                   size_t out_dims[3], uint32_t *out_bit_depth,
                   int32_t *out_is_signed, char *err_buf, size_t err_buf_len) {
  return decode_impl_c(codestream, codestream_len, out, out_len, out_ndim,
                       out_dims, out_bit_depth, out_is_signed, err_buf,
                       err_buf_len);
}

void openjph_free(void *ptr) { std::free(ptr); }

} // extern "C"
