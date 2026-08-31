use newsly_contract_codegen::generate_clients;

const OPENAPI: &str = r##"{
  "openapi": "3.1.0",
  "info": {"title": "fixture", "version": "1"},
  "paths": {},
  "components": {"schemas": {
    "Status": {"type": "string", "enum": ["ready", "unknown"]},
    "ContentResultKind": {"type": "string", "enum": ["content"]},
    "NoActionResultKind": {"type": "string", "enum": ["no_action"]},
    "ContentResult": {
      "type": "object",
      "required": ["result_kind", "content_id"],
      "properties": {
        "result_kind": {"$ref": "#/components/schemas/ContentResultKind"},
        "content_id": {"type": "integer"}
      }
    },
    "NoActionResult": {
      "type": "object",
      "required": ["result_kind"],
      "properties": {
        "result_kind": {"$ref": "#/components/schemas/NoActionResultKind"},
        "reason": {"type": ["string", "null"]}
      }
    },
    "ResultUnion": {
      "oneOf": [
        {"$ref": "#/components/schemas/ContentResult"},
        {"allOf": [{"$ref": "#/components/schemas/NoActionResult"}]}
      ],
      "discriminator": {
        "propertyName": "result_kind",
        "mapping": {
          "content": "#/components/schemas/ContentResult",
          "no_action": "#/components/schemas/NoActionResult"
        }
      }
    },
    "RootDto": {
      "type": "object",
      "required": ["created_at", "described_status", "nullable_status", "request_id", "result", "status"],
      "properties": {
        "created_at": {"type": "string", "format": "date-time"},
        "described_status": {
          "description": "A required ref wrapped to carry a description.",
          "oneOf": [{"$ref": "#/components/schemas/Status"}]
        },
        "details": {"type": ["object", "null"], "additionalProperties": {}},
        "nullable_status": {
          "oneOf": [
            {"$ref": "#/components/schemas/Status"},
            {"type": "null"}
          ]
        },
        "send_email": {"type": "boolean"},
        "request_id": {"type": "string"},
        "result": {"$ref": "#/components/schemas/ResultUnion"},
        "status": {"$ref": "#/components/schemas/Status"},
        "tags": {"type": "array", "items": {"type": "string"}}
      }
    }
  }}
}"##;

const POLICY: &str = r#"
version = 1

[[enums]]
schema = "Status"
targets = ["app_swift", "share_swift"]
open = true

[[models]]
schema = "RootDto"
targets = ["app_swift", "share_swift"]

[[unions]]
schema = "ResultUnion"
targets = ["app_swift", "share_swift"]
discriminator = "result_kind"
open = true

[settings]
untyped_fields = ["RootDto.details"]
"#;

#[test]
fn fixture_preserves_swift_open_enums_dates_presence_and_escape_hatches() {
    let generated = generate_clients(OPENAPI, POLICY).expect("generate fixture clients");

    assert!(
        generated
            .app_swift_contracts
            .contains("case unknownRaw(String)")
    );
    assert!(
        generated
            .app_swift_models
            .contains("struct APIRoot: Codable")
    );
    assert!(generated.app_swift_models.contains("let createdAt: Date"));
    assert!(
        generated
            .app_swift_models
            .contains("let describedStatus: APIStatus")
    );
    assert!(
        !generated
            .app_swift_models
            .contains("let describedStatus: APIStatus?")
    );
    assert!(
        generated
            .app_swift_models
            .contains("let nullableStatus: APIStatus?")
    );
    assert!(
        generated
            .app_swift_models
            .contains("nullableStatus: APIStatus?,")
    );
    assert!(
        !generated
            .app_swift_models
            .contains("nullableStatus: APIStatus? = nil")
    );
    assert!(generated.app_swift_models.contains(
        "nullableStatus = try container.decode(APIStatus?.self, forKey: .nullableStatus)"
    ));
    assert!(
        generated
            .app_swift_models
            .contains("try container.encode(nullableStatus, forKey: .nullableStatus)")
    );
    assert!(
        generated
            .app_swift_models
            .contains("details: [String: AnyCodable]? = nil")
    );
    assert!(
        generated
            .app_swift_models
            .contains("enum APIResultUnion: Codable")
    );
    assert!(
        generated
            .app_swift_models
            .contains("case content(APIContentResult)")
    );
    assert!(
        generated
            .app_swift_models
            .contains("case unknown(String, AnyCodable)")
    );
    assert!(!generated.app_swift_models.contains("let resultKind:"));
    assert!(!generated.app_swift_models.contains("APIContentResultKind"));
    assert!(
        generated
            .app_swift_models
            .contains("let details: [String: AnyCodable]?")
    );
    assert!(
        generated
            .app_swift_models
            .contains("case requestId = \"request_id\"")
    );
    assert!(
        generated
            .share_swift_models
            .contains("struct APIRoot: Codable")
    );
}

#[test]
fn tagged_union_rejects_unconstrained_discriminator_collisions() {
    let openapi = OPENAPI.replace(
        "\"result_kind\": {\"$ref\": \"#/components/schemas/ContentResultKind\"}",
        "\"result_kind\": {\"type\": \"string\"}",
    );
    let error = generate_clients(&openapi, POLICY).expect_err("collision must fail");
    assert!(
        error
            .to_string()
            .contains("field result_kind collides with its discriminator")
    );
}

#[test]
fn tagged_union_rejects_language_case_collisions() {
    let openapi = OPENAPI
        .replace(
            "\"ContentResultKind\": {\"type\": \"string\", \"enum\": [\"content\"]}",
            "\"ContentResultKind\": {\"type\": \"string\", \"enum\": [\"no-action\"]}",
        )
        .replace(
            "\"content\": \"#/components/schemas/ContentResult\"",
            "\"no-action\": \"#/components/schemas/ContentResult\"",
        );
    let error = generate_clients(&openapi, POLICY).expect_err("case collision must fail");
    assert!(error.to_string().contains("Swift case-name collision"));
}

#[test]
fn unreviewed_arbitrary_json_fails_closed() {
    let policy = POLICY.replace("untyped_fields = [\"RootDto.details\"]", "");
    let error = generate_clients(OPENAPI, &policy).expect_err("untyped field must fail");
    assert!(
        error
            .to_string()
            .contains("untyped JSON requires an explicit allowlist entry")
    );
}
