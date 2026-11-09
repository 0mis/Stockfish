// Isolated, no-search, batched test1757 FC2 sidecar. GPL-3.0-or-later.
#ifndef SF_TEST1757_FC2_ACTIVATION_COMMAND_H
#define SF_TEST1757_FC2_ACTIVATION_COMMAND_H
#if !defined(SF_TEST1757_FC2_DIAGNOSTIC) || !defined(NNUE_EMBEDDING_OFF) || defined(UNIVERSAL_BINARY)
    #error "Use only an isolated external-network FC2 diagnostic build"
#endif

#include <charconv>
#include <deque>
#include <filesystem>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>
#include "evaluate.h"
#include "movegen.h"
#include "nnue/network.h"
#include "nnue/nnue_accumulator.h"
#include "position.h"
#include "uci.h"

// Exact source defines this non-static helper in Stockfish (not Eval), with
// only a .cpp-local declaration. Declare the same function for this sidecar;
// its implementation and the ordinary evaluator remain unchanged.
namespace Stockfish { Value scale_evaluation(Value nnue, int optimism, const Position& pos); }

namespace Stockfish::FC2ActivationCommand {

inline void json_string(std::ostream& out, const std::string& text) {
    out << '"';
    for (unsigned char c : text)
    {
        if (c == '"' || c == '\\') out << '\\' << char(c);
        else if (c >= 32 && c < 127) out << char(c);
        else out << '?';  // Native FEN/path inputs here are ASCII; envelope owns other IDs.
    }
    out << '"';
}

template<typename T, std::size_t N>
inline void json_array(std::ostream& out, const std::array<T, N>& values) {
    out << '[';
    for (std::size_t i = 0; i < N; ++i)
    {
        if (i) out << ',';
        out << std::int64_t(values[i]);
    }
    out << ']';
}

inline bool capture_line(const std::string& text, const Eval::NNUE::Network& network) {
    // Each line is self-contained. Earlier game history before supplied_fen is
    // explicitly unknown; this is a board-activation API, not teacher provenance.
    std::istringstream input(text);
    std::string command, id, kind, field, fen;
    if (!(input >> command >> id >> kind) || command != "capture" || kind != "fen")
        return false;
    unsigned row_id = 0;
    const auto parsed = std::from_chars(id.data(), id.data() + id.size(), row_id);
    if (parsed.ec != std::errc{} || parsed.ptr != id.data() + id.size() || std::to_string(row_id) != id)
        return false;
    for (unsigned i = 0; i < 6; ++i)
    {
        if (!(input >> field)) return false;
        if (i) fen += ' ';
        fen += field;
    }
    std::deque<StateInfo> states(1);
    Position position;
    if (position.set(fen, false, &states.front())) return false;
    std::vector<std::string> moves;
    if (input >> field)
    {
        if (field != "moves") return false;
        while (input >> field)
        {
            if (moves.size() >= 4096 || MoveList<ALL>(position).size() == 0) return false;
            const Move move = UCIEngine::to_move(position, field);
            if (move == Move::none() || UCIEngine::move(move, false) != field) return false;
            states.emplace_back();
            position.do_move(move, states.back());
            moves.push_back(field);
        }
    }
    if (MoveList<ALL>(position).size() == 0) return false;
    auto ordinary_stack = std::make_unique<Eval::NNUE::AccumulatorStack>();
    auto ordinary_cache = std::make_unique<Eval::NNUE::AccumulatorCaches>(network);
    ordinary_stack->reset();
    const Value ordinary = network.evaluate(position, *ordinary_stack, *ordinary_cache);
    ordinary_stack.reset();
    ordinary_cache.reset();
    auto stack = std::make_unique<Eval::NNUE::AccumulatorStack>();
    auto cache = std::make_unique<Eval::NNUE::AccumulatorCaches>(network);
    stack->reset();
    Eval::NNUE::FC2ActivationCapture capture;
    network.capture_fc2_activations(position, *stack, *cache, capture);
    capture.ordinary_raw_nnue = ordinary;
    if (!capture.integer_reconstruction_valid()) return false;
    const Value ordinary_static_zero = Stockfish::scale_evaluation(ordinary, VALUE_ZERO, position);
    const int ordinary_static_cp = UCIEngine::to_cp(ordinary_static_zero, position);
    std::array<int, 49> offset_static_zero{}, offset_cp{};
    for (int delta = -24; delta <= 24; ++delta)
    {
        const std::int64_t shifted = std::int64_t(ordinary) + delta;
        if (shifted < std::numeric_limits<Value>::min() || shifted > std::numeric_limits<Value>::max()) return false;
        const Value value = Stockfish::scale_evaluation(static_cast<Value>(shifted), VALUE_ZERO, position);
        offset_static_zero[delta + 24] = value;
        offset_cp[delta + 24] = UCIEngine::to_cp(value, position);
    }
    std::ostringstream out;
    out << "{\"schema\":\"test1757-fc2-activation/1\",\"row_id\":" << row_id
        << ",\"source_commit\":\"7a17b53bcaf93f54a23ad65f309c413585500c82\",\"supplied_fen\":";
    json_string(out, fen);
    out << ",\"moves\":[";
    for (std::size_t i = 0; i < moves.size(); ++i) { if (i) out << ','; json_string(out, moves[i]); }
    out << "],\"final_fen\":";
    json_string(out, position.fen());
    out << ",\"history_scope\":\"supplied_fen_and_legal_prefix_only\",\"physical_head\":" << capture.physical_head
        << ",\"stored_heads\":" << capture.stored_heads
        << ",\"in_check\":" << (position.checkers() ? "true" : "false")
        << ",\"ordinary_eval_trace_available\":" << (position.checkers() ? "false" : "true")
        << ",\"side_to_move\":" << static_cast<int>(position.side_to_move())
        << ",\"rule50\":" << position.rule50_count() << ",\"fc0\":";
    json_array(out, capture.fc0);
    out << ",\"fc1\":"; json_array(out, capture.fc1);
    out << ",\"input_native\":"; json_array(out, capture.input_native);
    out << ",\"input_serialized\":"; json_array(out, capture.input_serialized);
    out << ",\"serialized_to_native\":"; json_array(out, capture.serialized_to_native);
    out << ",\"output_weights\":"; json_array(out, capture.output_weights);
    out << ",\"output_bias\":" << capture.output_bias << ",\"fc2_affine\":" << capture.fc2_affine
        << ",\"skip\":" << capture.skip << ",\"combined\":" << capture.combined
        << ",\"scaled\":" << capture.scaled << ",\"raw_nnue\":" << capture.raw_nnue
        << ",\"ordinary_raw_nnue\":" << capture.ordinary_raw_nnue
        << ",\"helper_static_zero\":" << ordinary_static_zero << ",\"helper_static_cp\":" << ordinary_static_cp
        << ",\"ordinary_static_zero\":";
    if (position.checkers()) out << "null"; else out << ordinary_static_zero;
    out << ",\"ordinary_static_cp\":";
    if (position.checkers()) out << "null"; else out << ordinary_static_cp;
    out << ",\"static_loss_available\":" << (position.checkers() ? "false" : "true")
        << ",\"static_loss_unavailable_reason\":";
    if (position.checkers()) out << "\"in_check\""; else out << "null";
    out << ",\"raw_offset_min\":-24,\"raw_offset_max\":24,\"cp_perspective\":\"side_to_move\",\"raw_offset_static_zero\":";
    json_array(out, offset_static_zero);
    out << ",\"raw_offset_cp\":"; json_array(out, offset_cp);
    out << ",\"integer_parity\":true}";
    std::cout << out.str() << '\n';
    std::cout.flush();
    return bool(std::cout);
}

inline int run(int argc, char* argv[]) {
    if (argc != 4 || std::string_view(argv[1]) != "--fc2-activations-v1" || std::string_view(argv[2]) != "--network")
        return 2;
    const auto path = path_from_utf8(argv[3]);
    if (!path.is_absolute()) return 2;
    auto network = std::make_unique<Eval::NNUE::Network>();
    Eval::NNUE::EvalFile loaded;
    network->load_external(std::filesystem::path{}, path, loaded);
    if (loaded.current != path) return 3;
    std::string line;
    std::size_t count = 0;
    while (std::getline(std::cin, line))
    {
        if (line == "quit") return std::cout ? 0 : 5;
        if (line.empty() || line.size() > 32768 || ++count > 100000 || !capture_line(line, *network))
        {
            std::cerr << "FC2 diagnostic rejected request or integer parity at row " << count << '\n';
            return 4;
        }
    }
    std::cerr << "FC2 diagnostic requires explicit quit\n";
    return 5;
}
}  // namespace Stockfish::FC2ActivationCommand
#endif
