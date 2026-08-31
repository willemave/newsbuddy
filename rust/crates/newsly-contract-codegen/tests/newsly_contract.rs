use newsly_contract_codegen::generate_clients;

const POLICY: &str = include_str!("../../../../contracts/client_codegen_policy.toml");
const OPENAPI: &str = include_str!("../../../../contracts/openapi/public.openapi.json");

#[test]
fn authoritative_openapi_generates_all_native_boundaries() {
    let generated = generate_clients(OPENAPI, POLICY).expect("generate clients");

    assert!(
        generated
            .app_swift_contracts
            .contains("enum APIContentStatus: Codable, Equatable, Hashable")
    );
    assert!(
        generated
            .app_swift_models
            .contains("struct APIContentDetailResponse: Codable")
    );
    assert!(
        generated
            .share_swift_models
            .contains("struct APIShareActionCreateRequest: Codable")
    );
    assert!(
        generated
            .share_swift_models
            .contains("struct APIShareActionResponse: Codable")
    );
    for model in [
        "struct APINewsItemSummaryResponse: Codable",
        "struct APINewsItemDetailResponse: Codable",
        "struct APINewsItemListResponse: Codable",
    ] {
        assert!(
            generated.app_swift_models.contains(model),
            "missing {model}"
        );
    }
}

#[test]
fn server_owned_timestamps_keep_native_date_types() {
    let generated = generate_clients(OPENAPI, POLICY).expect("generate clients");

    for field in [
        "let publicationDate: Date?",
        "let knowledgeSavedAt: Date?",
        "let updatedAt: Date?",
        "let checkedOutAt: Date?",
    ] {
        assert!(
            generated.app_swift_models.contains(field),
            "missing Swift date field {field}"
        );
    }
}

#[test]
fn reviewed_escape_hatches_are_language_native() {
    let generated = generate_clients(OPENAPI, POLICY).expect("generate clients");

    assert!(
        generated
            .app_swift_models
            .contains("let details: [String: AnyCodable]?")
    );
    assert!(
        generated
            .app_swift_models
            .contains("struct APINewsItemDetailResponse: Codable")
            && generated
                .app_swift_models
                .contains("let metadata: [String: AnyCodable]")
    );
}

#[test]
fn reviewed_compatibility_semantics_are_preserved() {
    let generated = generate_clients(OPENAPI, POLICY).expect("generate clients");

    assert!(
        generated
            .app_swift_contracts
            .contains("enum APISummaryVersion: Int, Codable, CaseIterable")
    );
    assert!(generated.app_swift_contracts.contains("case v1 = 1"));
    assert!(generated.app_swift_contracts.contains("case v2 = 2"));
    assert!(
        generated
            .app_swift_models
            .contains("let councilPersonas: [APICouncilPersonaInput]?")
    );
    assert!(
        generated
            .app_swift_models
            .contains("let discoveryRunId: Int?")
    );
    assert!(
        generated
            .app_swift_models
            .contains("let selectedSuggestionIds: [Int]")
    );
    assert!(generated.app_swift_models.contains(
        "isSubscribed = try container.decodeIfPresent(Bool.self, forKey: .isSubscribed) ?? false"
    ));
}
