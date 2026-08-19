# Production numeric contract

- Posterior: signed 11-bit two's-complement, saturating to `-1024..1023`.
- S1W message: five-bit sign/magnitude, sign bit 4 and magnitude bits 3:0,
  with negative zero decoded as zero.
- Check minima: non-negative magnitudes with lowest-index tie breaking.
- Prior: compiler-selected orbit class and signed fixed-point LLR table.
- Normalization: `m' = m - (m >> 2)` for the routed primary path.

The RTL schedule stores wider rescue metadata internally, but the production
wrapper selects profile 0 and the controller narrows its message limit
explicitly at the five-bit engine boundary. The host C tail has its own
quantized Relay-lite image and is validated independently against saved full
syndromes.
