"""Pure exact-text diagnostic overlay. Import/apply starts no process."""
import re

MACRO = "SF_TEST1757_FC2_DIAGNOSTIC"
TARGETS = ("src/main.cpp", "src/nnue/nnue_architecture.h", "src/nnue/network.h", "src/nnue/network.cpp")
HEADERS = ("src/fc2_activation_command.h", "src/nnue/fc2_activation_capture.h")


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Exact source anchor not unique: " + old[:100])
    return text.replace(old, new)


def apply(name, raw):
    text = raw.decode("utf-8")
    if MACRO in text:
        raise ValueError("Diagnostic already applied")
    if name == "src/main.cpp":
        text = once(text, '#include "uci.h"\n', '#include "uci.h"\n\n#if defined(' + MACRO + ')\n#include "fc2_activation_command.h"\n#endif\n')
        text = once(text, 'int main(int argc, char* argv[]) {\n', 'int main(int argc, char* argv[]) {\n#if defined(' + MACRO + ')\n    if (argc > 1 && std::string_view(argv[1]) == "--fc2-activations-v1")\n    {\n        Attacks::init();\n        Position::init();\n        return FC2ActivationCommand::run(argc, argv);\n    }\n#endif\n')
    elif name == "src/nnue/nnue_architecture.h":
        text = once(text, '#include "nnz_helper.h"\n', '#include "nnz_helper.h"\n\n#if defined(' + MACRO + ')\n#include "fc2_activation_capture.h"\n#endif\n')
        text = once(text, 'const NNZInfo<L1>&            nnzInfo) const {', 'const NNZInfo<L1>&            nnzInfo\n#if defined(' + MACRO + ')\n                  , FC2ActivationCapture* capture = nullptr\n#endif\n                  ) const {')
        anchor = '        return outputValue;\n'
        observer = '''#if defined(SF_TEST1757_FC2_DIAGNOSTIC)
        static_assert(FC_0_OUTPUTS == 32 && FC_1_OUTPUTS == 32);
        static_assert(decltype(fc_2)::InputDimensions == 128 && decltype(fc_2)::OutputDimensions == 1);
        if (capture)
        {
            for (unsigned i = 0; i < 32; ++i)
            {
                capture->fc0[i] = buffer.fc_0_out[i];
                capture->fc1[i] = buffer.fc_1_out[i];
            }
            for (unsigned i = 0; i < 128; ++i)
            {
                const auto physical = decltype(fc_2)::get_weight_index(i);
                capture->input_native[i] = buffer.concat_buffer[i];
                capture->input_serialized[i] = buffer.concat_buffer[physical];
                capture->serialized_to_native[i] = physical;
            }
            capture->fc2_affine = buffer.fc_2_out[0];
            capture->skip = skip_0;
            capture->combined = fwdOut;
            capture->scaled = outputValue;
            std::ostringstream readout(std::ios::out | std::ios::binary);
            if (fc_2.write_parameters(readout))
                capture->read_serialized_output(readout.str());
        }
#endif
'''
        text = once(text, anchor, observer + anchor)
    elif name == "src/nnue/network.h":
        anchor = '    void verify(const std::function<void(std::string_view)>& f,\n'
        declaration = '''#if defined(SF_TEST1757_FC2_DIAGNOSTIC)
    Value capture_fc2_activations(const Position& pos,
                                 AccumulatorStack& accumulatorStack,
                                 AccumulatorCaches& cache,
                                 FC2ActivationCapture& capture) const;
#endif

'''
        text = once(text, anchor, declaration + anchor)
    elif name == "src/nnue/network.cpp":
        begin = text.index('Value Network::evaluate(')
        end = text.index('\n}\n', begin) + 3
        original = text[begin:end]
        diagnostic = original.replace('Value Network::evaluate(', 'Value Network::capture_fc2_activations(', 1)
        diagnostic = once(diagnostic, 'AccumulatorCaches& cache) const {', 'AccumulatorCaches& cache,\n                        FC2ActivationCapture& capture) const {\n    capture = {};')
        diagnostic = once(diagnostic, 'network[bucket].propagate(transformedFeatures, nnzInfo)', 'network[bucket].propagate(transformedFeatures, nnzInfo, &capture)')
        diagnostic = once(diagnostic, '    return static_cast<Value>(positional / OutputScale);',
                          '    capture.physical_head = bucket;\n    capture.stored_heads = storedLayerStacks;\n    capture.raw_nnue = static_cast<Value>(positional / OutputScale);\n    return capture.raw_nnue;')
        text = text[:end] + '\n#if defined(' + MACRO + ')\n' + diagnostic + '#endif\n' + text[end:]
    else:
        raise ValueError("Out-of-scope production file")
    if tokens(strip_diagnostic(text)) != tokens(raw.decode("utf-8")):
        raise ValueError("Macro-disabled production tokens changed")
    return text.encode("utf-8")


def strip_diagnostic(text):
    return re.sub(r"(?m)^#if defined\(SF_TEST1757_FC2_DIAGNOSTIC\)\n.*?^#endif\n", "", text, flags=re.S)


def tokens(text):
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    return re.findall(r"\w+|[^\s]", text)
