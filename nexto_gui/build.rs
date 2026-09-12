fn main() {
    println!("cargo:rerun-if-changed=embedded_backend/backend.tar.gz");
    println!("cargo:rerun-if-changed=src/main.rs");
}
