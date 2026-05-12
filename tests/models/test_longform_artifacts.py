from app.models.metadata.longform_artifacts import LongformArtifactEnvelope


def _artifact_payload(extras: dict[str, object]) -> dict[str, object]:
    return {
        "title": "Artifact Title",
        "one_line": "This explains the source argument and why it matters for readers now.",
        "ask": "judge",
        "artifact": {
            "type": "argument",
            "payload": {
                "quotes": [
                    {
                        "text": "This first quote has enough source detail to be useful.",
                        "attribution": "Source A",
                    },
                    {
                        "text": "This second quote has enough source detail to be useful.",
                        "attribution": "Source B",
                    },
                ],
                "extras": extras,
                "key_points": [
                    {
                        "heading": f"Point {index}",
                        "content": (
                            "This point explains one concrete part of the argument with "
                            "enough detail for validation."
                        ),
                    }
                    for index in range(1, 5)
                ],
                "takeaway": "Readers should judge the claim by its evidence and tradeoffs.",
            },
        },
        "source_context": {
            "url": "https://example.com/source",
            "source_name": "Example",
            "publication_date": "2026-05-11",
            "platform": "web",
        },
        "selection_trace": {
            "source_hint": "article:general",
            "candidates": ["argument", "mental_model"],
            "selected": "argument",
            "reason": "The source is primarily making a claim for readers to judge.",
            "confidence": 0.8,
        },
        "feed_preview": {
            "title": "Feed Artifact Title",
            "one_line": "A concise preview of the argument and why it matters now.",
            "preview_bullets": ["One useful preview point"],
            "reason_to_read": "It gives enough evidence and caveats to judge the claim.",
            "artifact_type": "argument",
        },
    }


def test_argument_artifact_accepts_legacy_extras_without_shared_fields() -> None:
    envelope = LongformArtifactEnvelope.model_validate(
        _artifact_payload(
            {
                "thesis": "The source argues that execution quality matters more than raw demos.",
                "counterpoint": (
                    "A fair objection is that demos can still reveal meaningful capability."
                ),
            }
        )
    )

    extras = envelope.artifact.payload.extras
    assert extras.evidence == []
    assert extras.mental_model == []
    assert extras.counter_arguments == []
    assert extras.supporting_arguments == []


def test_argument_artifact_accepts_shared_extra_fields() -> None:
    envelope = LongformArtifactEnvelope.model_validate(
        _artifact_payload(
            {
                "thesis": "The source argues that execution quality matters more than raw demos.",
                "counterpoint": (
                    "A fair objection is that demos can still reveal meaningful capability."
                ),
                "evidence": ["The article cites measured adoption and concrete workflow changes."],
                "mental_model": ["Judge the system by repeated workflow reliability."],
                "counter_arguments": ["The source may underweight raw model capability gains."],
                "supporting_arguments": [
                    "The strongest support is the source's operational evidence."
                ],
            }
        )
    )

    extras = envelope.artifact.payload.extras
    assert extras.evidence == ["The article cites measured adoption and concrete workflow changes."]
    assert extras.mental_model == ["Judge the system by repeated workflow reliability."]
    assert extras.counter_arguments == ["The source may underweight raw model capability gains."]
    assert extras.supporting_arguments == [
        "The strongest support is the source's operational evidence."
    ]


def test_artifact_payload_accepts_legacy_overview() -> None:
    payload = _artifact_payload(
        {
            "thesis": "The source argues that execution quality matters more than raw demos.",
            "counterpoint": (
                "A fair objection is that demos can still reveal meaningful capability."
            ),
        }
    )
    artifact = payload["artifact"]
    assert isinstance(artifact, dict)
    artifact_payload = artifact["payload"]
    assert isinstance(artifact_payload, dict)
    artifact_payload["overview"] = (
        "The older payload includes a narrative overview that is still accepted for "
        "already-generated artifacts, but new prompts no longer request this field."
    )

    envelope = LongformArtifactEnvelope.model_validate(payload)

    assert envelope.artifact.payload.overview is not None
