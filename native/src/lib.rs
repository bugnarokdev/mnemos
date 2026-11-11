mod chain;
mod engine;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

use crate::chain::content_hash;

fn py_err(err: impl std::fmt::Display) -> PyErr {
    PyRuntimeError::new_err(err.to_string())
}

#[pyclass]
struct Engine {
    inner: std::sync::Mutex<engine::Engine>,
}

#[pymethods]
impl Engine {
    #[new]
    fn new(db_path: String, secret: String, config_json: String) -> PyResult<Self> {
        let inner = engine::Engine::open(&db_path, &secret, &config_json).map_err(py_err)?;
        Ok(Self {
            inner: std::sync::Mutex::new(inner),
        })
    }

    fn remember(&self, text: &str, session_id: &str, kind: &str) -> PyResult<String> {
        let mut engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
        let value = engine.remember(text, session_id, kind).map_err(py_err)?;
        Ok(value.to_string())
    }

    fn recall(&self, query: &str, limit: usize) -> PyResult<String> {
        let engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
        Ok(engine.recall(query, limit).to_string())
    }

    fn checkpoint(&self, py: Python<'_>, entries_json: &str, idem_key: &str, session_id: &str) -> PyResult<String> {
        let entries_json = entries_json.to_string();
        let idem_key = idem_key.to_string();
        let session_id = session_id.to_string();
        py.detach(move || {
            let mut engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
            let value = engine
                .checkpoint(&entries_json, &idem_key, &session_id)
                .map_err(py_err)?;
            Ok(value.to_string())
        })
    }

    fn flush(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            let mut engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
            let value = engine.flush(None).map_err(py_err)?;
            Ok(value.to_string())
        })
    }

    fn restore(&self, py: Python<'_>, object_id: &str) -> PyResult<String> {
        let object_id = object_id.to_string();
        py.detach(move || {
            let mut engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
            let value = engine.restore(&object_id).map_err(py_err)?;
            Ok(value.to_string())
        })
    }

    fn doctor(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            let engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
            let value = engine.doctor().map_err(py_err)?;
            Ok(value.to_string())
        })
    }

    fn status(&self) -> PyResult<String> {
        let engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
        Ok(engine.status().to_string())
    }

    fn init_chain(&self, py: Python<'_>, modules_json: &str) -> PyResult<String> {
        let modules_json = modules_json.to_string();
        py.detach(move || {
            let mut engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
            let value = engine.init_chain(&modules_json).map_err(py_err)?;
            Ok(value.to_string())
        })
    }

    fn faucet(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            let engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
            let value = engine.faucet().map_err(py_err)?;
            Ok(value.to_string())
        })
    }

    fn pubkey_hex(&self) -> String {
        let engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
        engine.pubkey_hex()
    }

    fn sui_address(&self) -> String {
        let engine = self.inner.lock().unwrap_or_else(|err| err.into_inner());
        engine.sui_address()
    }

    fn close(&self) {}
}

#[pyfunction]
fn content_hash_hex(prev_hex: &str, blob_sha_hex: &str, version: u64, session_id: &str, blob_id: &str) -> PyResult<String> {
    let prev = parse_hex32(prev_hex)?;
    let blob_sha = parse_hex32(blob_sha_hex)?;
    Ok(hex::encode(content_hash(&prev, &blob_sha, version, session_id, blob_id)))
}

#[pyfunction]
fn seal_blob_hex(secret: &str, plaintext: &str, nonce_hex: &str) -> PyResult<String> {
    let keys = chain::derive_keys(secret).map_err(py_err)?;
    let nonce_bytes = hex::decode(nonce_hex).map_err(py_err)?;
    let nonce: [u8; 24] = nonce_bytes
        .try_into()
        .map_err(|_| py_err("nonce must be 24 bytes"))?;
    let blob = chain::seal(&keys.enc_key, plaintext.as_bytes(), &nonce).map_err(py_err)?;
    Ok(hex::encode(blob))
}

#[pyfunction]
fn open_blob_text(secret: &str, blob_hex: &str) -> PyResult<String> {
    let keys = chain::derive_keys(secret).map_err(py_err)?;
    let blob = hex::decode(blob_hex).map_err(py_err)?;
    let plain = chain::open_blob(&keys.enc_key, &blob).map_err(py_err)?;
    String::from_utf8(plain).map_err(py_err)
}

/// Public identity derived from a secret, for cross-backend checks.
#[pyfunction]
fn public_identity(secret: &str) -> PyResult<String> {
    use sui_crypto::ed25519::Ed25519PrivateKey;
    let keys = chain::derive_keys(secret).map_err(py_err)?;
    let legacy = chain::derive_legacy_keys(secret).map_err(py_err)?;
    let sui = Ed25519PrivateKey::new(keys.sui_seed).public_key().derive_address();
    Ok(serde_json::json!({
        "kdf": "argon2id",
        "pubkey_hex": hex::encode(keys.pubkey),
        "sui_address": sui.to_string(),
        "legacy_pubkey_hex": hex::encode(legacy.pubkey),
    })
    .to_string())
}

fn parse_hex32(value: &str) -> PyResult<[u8; 32]> {
    let bytes = hex::decode(value).map_err(py_err)?;
    bytes
        .try_into()
        .map_err(|_| py_err("expected 32 bytes"))
}

#[pymodule]
fn mnemos_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Engine>()?;
    m.add_function(wrap_pyfunction!(content_hash_hex, m)?)?;
    m.add_function(wrap_pyfunction!(seal_blob_hex, m)?)?;
    m.add_function(wrap_pyfunction!(open_blob_text, m)?)?;
    m.add_function(wrap_pyfunction!(public_identity, m)?)?;
    Ok(())
}
