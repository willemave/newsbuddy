import Foundation

struct ArticleMetadata: Decodable {
    let sourceMetadata: SourceMetadata?

    private enum CodingKeys: String, CodingKey {
        case sourceMetadata = "source_metadata"
    }
}
