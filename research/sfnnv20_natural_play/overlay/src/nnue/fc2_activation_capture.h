// Diagnostic-only current SFNNv20 FC2 observer. GPL-3.0-or-later.
// Adapted from the public f5ef3e3d FC2 observer; never a search hook.
#ifndef SF_SFNNV20_FC2_ACTIVATION_CAPTURE_H
#define SF_SFNNV20_FC2_ACTIVATION_CAPTURE_H

#if !defined(SF_SFNNV20_FC2_DIAGNOSTIC) || !defined(USE_AVX2) \
 || !defined(USE_PAIR_ACTIVATIONS) || defined(USE_AVX512)
    #error "FC2 capture requires the isolated AVX2 pair-activation diagnostic"
#endif

#include <array>
#include <cstdint>
#include <limits>
#include <sstream>
#include <string>

namespace Stockfish::Eval::NNUE {

struct FC2ActivationCapture {
    int physical_head = -1, stored_heads = 0;
    std::array<std::int32_t, 32> fc0{}, fc1{};
    std::array<std::uint8_t, 128> input_native{}, input_serialized{};
    std::array<unsigned, 128> serialized_to_native{};
    std::array<int, 128> output_weights{};
    std::int32_t output_bias = 0, fc2_affine = 0, skip = 0, combined = 0, scaled = 0;
    std::int32_t raw_nnue = 0, ordinary_raw_nnue = 0;
    bool parameters_ready = false;

    bool read_serialized_output(const std::string& bytes) {
        // Existing AffineTransform::write_parameters is the authority for
        // serialized order; never expose in-memory scrambled weights as W2.
        if (bytes.size() != 132)
            return false;
        std::uint32_t bits = 0;
        for (unsigned i = 0; i < 4; ++i)
            bits |= std::uint32_t(static_cast<unsigned char>(bytes[i])) << (8 * i);
        const std::int64_t signed_bias = bits < (std::uint32_t(1) << 31)
            ? std::int64_t(bits) : std::int64_t(bits) - (std::int64_t(1) << 32);
        output_bias = static_cast<std::int32_t>(signed_bias);
        for (unsigned i = 0; i < 128; ++i)
        {
            const int byte = static_cast<unsigned char>(bytes[i + 4]);
            output_weights[i] = byte < 128 ? byte : byte - 256;
        }
        parameters_ready = true;
        return true;
    }

    bool integer_reconstruction_valid() const {
        if (!parameters_ready || stored_heads != 92 || physical_head < 0 || physical_head >= stored_heads)
            return false;
        std::int64_t affine = output_bias;
        for (unsigned i = 0; i < 128; ++i)
        {
            if (input_native[i] > 127 || input_serialized[i] > 127
                || serialized_to_native[i] >= 128
                || input_serialized[i] != input_native[serialized_to_native[i]])
                return false;
            affine += std::int64_t(output_weights[i]) * input_serialized[i];
        }
        const std::int64_t actual_skip = std::int64_t(fc0[30]) - fc0[31];
        const std::int64_t forwarded = affine + actual_skip;
        const auto fits = [](std::int64_t x) {
            return x >= std::numeric_limits<std::int32_t>::min()
                && x <= std::numeric_limits<std::int32_t>::max();
        };
        return fits(affine) && fits(actual_skip) && fits(forwarded)
            && affine == fc2_affine && actual_skip == skip && forwarded == combined
            && (forwarded * 9600) / 16384 == scaled
            && scaled / 16 == raw_nnue && raw_nnue == ordinary_raw_nnue;
    }
};
}  // namespace Stockfish::Eval::NNUE
#endif
