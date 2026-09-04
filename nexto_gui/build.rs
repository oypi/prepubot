fn main() {
    println!("cargo:rerun-if-changed=embedded_backend/nexto_backend");
    println!("cargo:rerun-if-changed=src/main.rs");
}
