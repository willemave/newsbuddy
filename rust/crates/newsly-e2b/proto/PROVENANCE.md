# E2B envd process protocol provenance

- Repository: `https://github.com/e2b-dev/infra`
- Path: `packages/envd/spec/process/process.proto`
- Commit: `e19a12b8fc5d318c6e88a8edba0a94d1f153a841`
- Selection: the commit pinned by `e2b-dev/E2B/spec/infra-ref` at E2B commit
  `5a56c87e9db0e221b138662805af7743e75f1082`
- Upstream SHA-256: `8edd9358c7dbfcad96796b3f0ed8d14c262b8b14d6bc7d5e84d468941511b8e0`
- Vendored SHA-256: `96bc8ee16385d3a72233cda9d25e4846f0029d6f144c1523ba32d3ce62680ccd`
- Normalization: trailing spaces were stripped; protocol tokens and descriptor semantics are
  unchanged.

Only the process descriptor is vendored. Files use envd's documented bounded REST stream, so the
filesystem protobuf service is deliberately not copied. Update this file, the checksum, generated
bindings, recordings, and live protocol canaries together; never auto-upgrade the descriptor.
