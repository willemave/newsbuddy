use std::fmt::Write as _;
use std::path::PathBuf;
use std::process::Command;

use sha2::{Digest, Sha256};

const PROCESS_PROTO_SHA256: &str =
    "96bc8ee16385d3a72233cda9d25e4846f0029d6f144c1523ba32d3ce62680ccd";

fn main() {
    println!("cargo:rerun-if-changed=proto/process.proto");
    println!("cargo:rerun-if-changed=proto/PROVENANCE.md");
    let source = std::fs::read("proto/process.proto").expect("vendored process proto must exist");
    let mut actual_checksum = String::with_capacity(PROCESS_PROTO_SHA256.len());
    for byte in Sha256::digest(&source) {
        write!(&mut actual_checksum, "{byte:02x}").expect("writing to a String cannot fail");
    }
    assert_eq!(
        actual_checksum, PROCESS_PROTO_SHA256,
        "vendored E2B process proto changed without updating its pinned provenance"
    );

    let out_dir = PathBuf::from(std::env::var_os("OUT_DIR").expect("Cargo must set OUT_DIR"));
    let descriptor = out_dir.join("e2b-process-descriptor.bin");
    let protoc = protoc_bin_vendored::protoc_bin_path().expect("vendored protoc must be available");
    let output = Command::new(protoc)
        .args([
            "--include_imports",
            "--include_source_info",
            "--proto_path=proto",
        ])
        .arg(format!("--descriptor_set_out={}", descriptor.display()))
        .arg("proto/process.proto")
        .output()
        .expect("vendored protoc must start");
    assert!(
        output.status.success(),
        "vendored protoc failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    connectrpc_build::Config::new()
        .files(&["process.proto"])
        .descriptor_set(descriptor)
        .include_file("_connectrpc.rs")
        .compile()
        .expect("vendored E2B process protocol must compile");
}
