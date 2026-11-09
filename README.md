# Experimental net-only build qualification

This isolated driver builds official Stockfish43e01 and a direct child whose only source change selects the previously released d5 nine-bias NNUE. It verifies both public net SHA256s, exact embedded bytes, two fresh default benchmark signatures per side and start-position perft4. Search source is official on both sides. This is functional Fishtest preparation, not a strength test or a test of the earlier combined history/prefetch release.

Stockfish source and all upstream authors remain unchanged except the default-net line. Shawn Xu trained test1757 (CC0); 0mis made the fixed readout-bias derivative with AI assistance. The exact public sources and full GPL corresponding-source archives accompany the binaries in the run artifact. No private formal openings, custom secrets, paid runners, games or farm submissions are used. Only this exact driver branch can trigger this workflow.
