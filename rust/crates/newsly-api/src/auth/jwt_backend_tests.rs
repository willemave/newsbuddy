//! Guards the jsonwebtoken crypto backend: Apple identity tokens are RS256 (verified from a JWK)
//! and the Apple revocation client secret is ES256 (signed from a PKCS#8 `.p8` key).
//! The keys below were generated solely for these tests and protect nothing.

use jsonwebtoken::jwk::JwkSet;
use jsonwebtoken::{Algorithm, DecodingKey, EncodingKey, Header, Validation, decode, encode};
use serde_json::{Value, json};

const TEST_EC_P256_PKCS8: &str = "-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg80j8J+160WOaPDfs
mlXKNfYmKRsIhWaATwJOKokzDJihRANCAASUDNXBkqXxB2p15bVDrKkkTP6JU7e5
Nw6fXOU54BsFVwtxho+uyBy79Tfx/ETCcTmOpZUReyr6gx12FybLQlBT
-----END PRIVATE KEY-----";

const TEST_RSA_2048_PKCS8: &str = "-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCx+0yPIeZ4CQtd
GKw2xVi3hIVBS7XdYzP4x6Je0kdwRbWtV8kTieX9omV8sYWS0EOgcGHRwwd4tQtM
bYNx821vYxyJEV893nh/zKPWUS2lYRBd3LejTnRAD98XWLwtdrj/8wrUv7kJxgn/
gNl9QDohEbRVEKbX6VdCNxK7ttjhs6mV1inToYC95ami+cOZ8wyNouChtEzAS5H0
p4mc0fdvaEEZBQZ4tCkmakQ5XXNaDnFVzsgM5N8waezYKYiE3qSF1ZhKJdFChU/A
GrhHk0gECLNH93H6bnGopQ+iZBHeE8+Ov7eMLAWLZnOCAvqUwzQMzLOEr4myoF0r
Mo3UqKMdAgMBAAECggEAAxi8dwQPurhrkHHYszZBSiYd3Go5DpJGifZNOqmdcRRh
ahPsLXEZhY1xBxeem5SQjKbP6OKkC2NZADoqjo298nFuwmrBNbYcchrJIfY1HVrA
OmXX8xkvGBs+li0mzwKxCtD7eAKi8UrXCzE+d3mp2T/x24YH5RtL95y2MHposRqT
86UikMv7qlK+IKQteuHL/p94wIG7YKkBQCcpdEvS7XpbsCKKIIWQuX/+8vn7A/rN
28Tc00MJTy2fFTUfSY0YG2qTIA9QyQSJfkZqUnmfFafD5LAvqFCVTzYQ546L3Mgu
rZDAwSQLAaXcoACYETLi3uImk6B9g8Fq41OYSS64UQKBgQDZtxIpLE/fN+jdohna
LxQTmsZTna92F5J821aLEH3OvqWtSlF7ZK6ez69Yd9yyNdcjp5LjVYA9wwS8SHtX
fZU0tNrcyHjJkHoYNR83OUYbR189+EoRtKxvqcs7D7NJkz3DH0wfUv7ZgEhwVt5k
FG482Y78dHl5pEm/BSZEBBOw8QKBgQDRR4mGpYMIOZ4tEyj2mBqqDPf4K0GJ9M2I
dnPTZSUhKFxjFv/Rj69FvFQbE/iIksXK8SUj8dp7xfsa7yoROJgJYVsmrk+claB1
forjsot2ps5EbyWxbvU5jWNRbimILTDCrwEFh0vI70BNLQCJwCjyFo4LFK+3bkbB
50z6jksU7QKBgCdOXgEA9wqd1XTuJyy1AzLm5peCCIORjejs0JsaxxvkX5UBreRR
MRWK+WVHfpjOD6FoR6Szcwuzgc9cZqhNC/sQC9OAfg4l/2UPcitOM9II+LD/ep/n
tpoSVZa8JupJowTvSZb+LPSnrGxbwpre9WgW/zGk9al4d7f8/gOZmNSBAoGBAKZ6
x/YqmA/k9eVZho6T9TItWt06W8qIyS+zOvSBU4YK+2OAGkT0jjNlW2qm+mB6kYmd
RzRO4sTve4+zjb1hTeC4Z7BfncksZhbRcV8s2zCqBQ//cvaxE7aSuN1ASx2fBVj5
qlBhjnHrkTVThUgPawMZ2ikKbbX3raX2gBAZPbUJAoGBAMBkkSJ4HyDGQWhi620E
eYLeTHDpnqEKhB24eTh7WO03Pqvn5Y42azNtlp+DLkI52csTbgCNMr32gDM/Sa6v
BBGSgCltwZXrl1GgLREIt5yiznSpuelHs7Og0uKgDzu5S6kxBvtceilMEJy2wc5B
GaEm2emL8bUOTZZUYbujQoMW
-----END PRIVATE KEY-----";

const TEST_RSA_MODULUS: &str = "sftMjyHmeAkLXRisNsVYt4SFQUu13WMz-MeiXtJHcEW1rVfJE4nl_aJlfLGFktBDoHBh0cMHeLULTG2DcfNtb2MciRFfPd54f8yj1lEtpWEQXdy3o050QA_fF1i8LXa4__MK1L-5CcYJ_4DZfUA6IRG0VRCm1-lXQjcSu7bY4bOpldYp06GAveWpovnDmfMMjaLgobRMwEuR9KeJnNH3b2hBGQUGeLQpJmpEOV1zWg5xVc7IDOTfMGns2CmIhN6khdWYSiXRQoVPwBq4R5NIBAizR_dx-m5xqKUPomQR3hPPjr-3jCwFi2ZzggL6lMM0DMyzhK-JsqBdKzKN1KijHQ";

#[test]
fn rs256_tokens_verify_against_a_jwk_set() {
    let claims = json!({"iss": "https://appleid.apple.com", "aud": "com.example.app", "sub": "user", "exp": 4_102_444_800_u64});
    let mut header = Header::new(Algorithm::RS256);
    header.kid = Some("test-key".to_owned());
    let token = encode(
        &header,
        &claims,
        &EncodingKey::from_rsa_pem(TEST_RSA_2048_PKCS8.as_bytes()).unwrap(),
    )
    .unwrap();
    let keys: JwkSet = serde_json::from_value(json!({"keys": [{
        "kty": "RSA", "kid": "test-key", "use": "sig", "alg": "RS256",
        "n": TEST_RSA_MODULUS, "e": "AQAB"
    }]}))
    .unwrap();
    let key = DecodingKey::from_jwk(keys.find("test-key").unwrap()).unwrap();
    let mut validation = Validation::new(Algorithm::RS256);
    validation.set_audience(&["com.example.app"]);
    let decoded = decode::<Value>(&token, &key, &validation).unwrap();
    assert_eq!(decoded.claims["sub"], "user");
}

#[test]
fn es256_client_secrets_sign_from_pkcs8_keys() {
    let key = EncodingKey::from_ec_pem(TEST_EC_P256_PKCS8.as_bytes()).unwrap();
    let token = encode(
        &Header::new(Algorithm::ES256),
        &json!({"iss": "team"}),
        &key,
    )
    .unwrap();
    assert_eq!(token.split('.').count(), 3);
}
