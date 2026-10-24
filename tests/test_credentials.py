"""Tests for the credential store (encrypted-file fallback backend)."""

from pyfix360.credentials import store as cred_store


def test_roundtrip_file_backend(data_dir, no_keyring):
    info = cred_store.set_credential("provider-1", "s3cr3t-value")
    assert info.backend == "file-encrypted"
    assert info.warning  # weakness must be surfaced
    assert cred_store.get_credential("provider-1") == "s3cr3t-value"


def test_secret_not_stored_in_plaintext(data_dir, no_keyring):
    cred_store.set_credential("provider-1", "s3cr3t-value")
    vault = data_dir / "credentials.vault"
    raw = vault.read_bytes()
    assert b"s3cr3t-value" not in raw


def test_delete_credential(data_dir, no_keyring):
    cred_store.set_credential("p", "v")
    cred_store.delete_credential("p")
    assert cred_store.get_credential("p") is None


def test_missing_credential_returns_none(data_dir, no_keyring):
    assert cred_store.get_credential("does-not-exist") is None


def test_fingerprint_is_stable_and_non_reversible():
    fp1 = cred_store.fingerprint("secret")
    fp2 = cred_store.fingerprint("secret")
    assert fp1 == fp2
    assert "secret" not in fp1
    assert len(fp1) == 12
