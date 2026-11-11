//! Snapshot hashing, XChaCha20-Poly1305, and Ed25519.
//!
//! The byte layout matches `mnemos/chain.py`. A content hash is SHA-256 over
//! `MNEMOS1\\0 || prev || blob_sha || version_le || session || blob_id`.
//! The signature covers `mnemos-snapshot-v1 || content_hash`.

use anyhow::{anyhow, Result};
use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{XChaCha20Poly1305, XNonce};
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use hkdf::Hkdf;
use sha2::{Digest, Sha256};

pub const DOMAIN: &[u8] = b"mnemos-snapshot-v1";
pub const PREIMAGE_MAGIC: &[u8] = b"MNEMOS1\x00";
pub const BLOB_MAGIC: &[u8] = b"MNMS";
pub const BLOB_VERSION: u8 = 1;
pub const BLOB_AAD: &[u8] = b"mnemos-blob-v1";
pub const GENESIS_PREV: [u8; 32] = [0u8; 32];

pub struct Keys {
    pub enc_key: [u8; 32],
    pub sign_key: SigningKey,
    pub pubkey: [u8; 32],
    pub sui_seed: [u8; 32],
}

fn hkdf32(secret: &str, salt: &[u8], info: &[u8]) -> Result<[u8; 32]> {
    let hk = Hkdf::<Sha256>::new(Some(salt), secret.as_bytes());
    let mut out = [0u8; 32];
    hk.expand(info, &mut out)
        .map_err(|_| anyhow!("hkdf expand failed"))?;
    Ok(out)
}

pub fn derive_keys(secret: &str) -> Result<Keys> {
    if secret.is_empty() {
        return Err(anyhow!("Mnemos secret is empty"));
    }
    let enc_key = hkdf32(secret, b"mnemos-aead-v1", b"xchacha20-poly1305")?;
    let sign_seed = hkdf32(secret, b"mnemos-sign-v1", b"ed25519-seed")?;
    let sui_seed = hkdf32(secret, b"mnemos-sui-v1", b"ed25519-seed")?;
    let sign_key = SigningKey::from_bytes(&sign_seed);
    let pubkey = sign_key.verifying_key().to_bytes();
    Ok(Keys {
        enc_key,
        sign_key,
        pubkey,
        sui_seed,
    })
}

pub fn sha256(data: &[u8]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(data);
    hasher.finalize().into()
}

pub fn content_preimage(
    prev: &[u8; 32],
    blob_sha: &[u8; 32],
    version: u64,
    session_id: &str,
    blob_id: &str,
) -> Vec<u8> {
    let session = session_id.as_bytes();
    let blob = blob_id.as_bytes();
    let mut out = Vec::with_capacity(8 + 32 + 32 + 8 + 8 + session.len() + blob.len());
    out.extend_from_slice(PREIMAGE_MAGIC);
    out.extend_from_slice(prev);
    out.extend_from_slice(blob_sha);
    out.extend_from_slice(&version.to_le_bytes());
    out.extend_from_slice(&(session.len() as u32).to_le_bytes());
    out.extend_from_slice(session);
    out.extend_from_slice(&(blob.len() as u32).to_le_bytes());
    out.extend_from_slice(blob);
    out
}

pub fn content_hash(
    prev: &[u8; 32],
    blob_sha: &[u8; 32],
    version: u64,
    session_id: &str,
    blob_id: &str,
) -> [u8; 32] {
    sha256(&content_preimage(prev, blob_sha, version, session_id, blob_id))
}

pub fn signing_message(content: &[u8; 32]) -> Vec<u8> {
    let mut msg = Vec::with_capacity(DOMAIN.len() + 32);
    msg.extend_from_slice(DOMAIN);
    msg.extend_from_slice(content);
    msg
}

pub fn sign_hash(key: &SigningKey, content: &[u8; 32]) -> [u8; 64] {
    key.sign(&signing_message(content)).to_bytes()
}

pub fn verify_hash(pubkey: &[u8; 32], content: &[u8; 32], signature: &[u8; 64]) -> bool {
    let Ok(vk) = VerifyingKey::from_bytes(pubkey) else {
        return false;
    };
    let sig = Signature::from_bytes(signature);
    vk.verify(&signing_message(content), &sig).is_ok()
}

pub fn seal(enc_key: &[u8; 32], plaintext: &[u8], nonce: &[u8; 24]) -> Result<Vec<u8>> {
    let cipher = XChaCha20Poly1305::new(enc_key.into());
    let body = cipher
        .encrypt(
            XNonce::from_slice(nonce),
            Payload {
                msg: plaintext,
                aad: BLOB_AAD,
            },
        )
        .map_err(|_| anyhow!("encrypt failed"))?;
    let mut out = Vec::with_capacity(4 + 1 + 24 + body.len());
    out.extend_from_slice(BLOB_MAGIC);
    out.push(BLOB_VERSION);
    out.extend_from_slice(nonce);
    out.extend_from_slice(&body);
    Ok(out)
}

pub fn open_blob(enc_key: &[u8; 32], blob: &[u8]) -> Result<Vec<u8>> {
    if blob.len() < 4 + 1 + 24 + 16 || &blob[..4] != BLOB_MAGIC {
        return Err(anyhow!("not a Mnemos blob"));
    }
    if blob[4] != BLOB_VERSION {
        return Err(anyhow!("unsupported blob version {}", blob[4]));
    }
    let nonce: [u8; 24] = blob[5..29].try_into().unwrap();
    let cipher = XChaCha20Poly1305::new(enc_key.into());
    cipher
        .decrypt(
            XNonce::from_slice(&nonce),
            Payload {
                msg: &blob[29..],
                aad: BLOB_AAD,
            },
        )
        .map_err(|_| anyhow!("decrypt failed"))
}

#[derive(Clone, Debug)]
pub struct Entry {
    pub id: String,
    pub kind: String,
    pub session_id: String,
    pub text: String,
    pub ts: i64,
}

pub fn canonical_plaintext(entries: &[Entry]) -> Vec<u8> {
    let mut rows = entries.to_vec();
    rows.sort_by(|left, right| left.id.cmp(&right.id));
    let mut encoded = Vec::new();
    for entry in rows {
        encoded.push(format!(
            "{{\"id\":{},\"kind\":{},\"session_id\":{},\"text\":{},\"ts\":{}}}",
            serde_json::to_string(&entry.id).unwrap(),
            serde_json::to_string(&entry.kind).unwrap(),
            serde_json::to_string(&entry.session_id).unwrap(),
            serde_json::to_string(&entry.text).unwrap(),
            entry.ts
        ));
    }
    format!("{{\"entries\":[{}],\"v\":1}}", encoded.join(",")).into_bytes()
}

pub fn random_nonce() -> Result<[u8; 24]> {
    let mut nonce = [0u8; 24];
    getrandom::getrandom(&mut nonce).map_err(|err| anyhow!("random nonce: {err}"))?;
    Ok(nonce)
}
