//
//  AnyCodable+Decoding.swift
//  newsly
//
//  Shared helpers for re-decoding an `[String: AnyCodable]` payload (typically a
//  `metadata`/`extras`-style dictionary field) into a concrete `Decodable` type.
//  Consolidates the JSONSerialization + JSONDecoder round-trip that previously lived
//  independently in ContentSummary.swift and ContentDetail.swift.
//

import Foundation

enum AnyCodableDecoding {
    private static func makeDecoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }

    /// Re-decodes `raw` into `T`, propagating any serialization/decoding error.
    /// Use where a malformed payload should surface as a decode failure rather than
    /// be silently swallowed (e.g. required-field decode paths).
    static func decode<T: Decodable>(_ type: T.Type, from raw: [String: AnyCodable]?) throws -> T? {
        guard let raw else { return nil }
        let data = try JSONSerialization.data(withJSONObject: raw.mapValues(\.value))
        return try makeDecoder().decode(type, from: data)
    }

    /// Re-decodes `raw` into `T`, returning `nil` on any serialization/decoding failure.
    /// Lenient by design: callers use this for presentation-only payloads (e.g. optional
    /// feed-preview/summary-format projections) where a malformed shape should fall back
    /// to "not present" instead of failing the whole decode.
    static func decodeLenient<T: Decodable>(_ type: T.Type, from raw: [String: AnyCodable]?) -> T? {
        guard let raw else { return nil }
        guard let data = try? JSONSerialization.data(withJSONObject: raw.mapValues(\.value)) else {
            return nil
        }
        return try? makeDecoder().decode(type, from: data)
    }
}
