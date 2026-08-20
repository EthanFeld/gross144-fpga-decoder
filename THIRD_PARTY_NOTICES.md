# Third-party references and provenance

MITTEN does not vendor the Relay-BP source tree or its Gross144 Stim fixtures.
The reproducibility workflow downloads the public fixture into ignored
`build/relay` and pins it to commit
`19d7023d476248858fc01bdf087ce673feaa4ef4`.

## Relay-BP

- Repository: https://github.com/trmue/relay
- License: Apache License 2.0
- Algorithm paper: Tristan Müller et al., *Improved Belief Propagation Is
  Sufficient for Real-Time Decoding of Quantum Memory*, arXiv:2506.01779,
  https://arxiv.org/abs/2506.01779

MITTEN's FPGA/common-path and C-tail implementation is a separate project. It
uses the public Relay-BP paper and public test circuits as algorithmic/reference
inputs and fixture provenance.

## Gross `[[144,12,12]]` bivariate-bicycle code

- Sergey Bravyi, Andrew W. Cross, Jay M. Gambetta, Dmitri Maslov, Patrick Rall,
  and Theodore J. Yoder, *High-threshold and low-overhead fault-tolerant quantum
  memory*, Nature 627, 778–782 (2024), arXiv:2308.07915,
  https://arxiv.org/abs/2308.07915
- Public reference implementation/data:
  https://github.com/sbravyi/BivariateBicycleCodes

## FPGA comparison paper

- Thilo Maurer et al., *Real-time decoding of the gross code memory with
  FPGAs*, arXiv:2510.21600, https://arxiv.org/abs/2510.21600

The performance figures labeled "paper reference" in this repository are
comparison values, not measurements produced by MITTEN.

Third-party projects retain their own copyrights and licenses. MITTEN's own
source is licensed under the Apache License 2.0; see [`LICENSE`](LICENSE).
