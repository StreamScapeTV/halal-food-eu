import Foundation

struct SearchProducts: Sendable {
    static let defaultPageSize = 25
    static let maximumPageSize = 50
    static let maximumQueryCharacters = 160

    private let catalog: any ProductSearchCatalog

    init(catalog: any ProductSearchCatalog) {
        self.catalog = catalog
    }

    func callAsFunction(
        _ query: String,
        limit: Int = defaultPageSize,
        offset: Int = 0
    ) async throws -> ProductSearchPage {
        let whitespaceNormalized = query
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")

        guard whitespaceNormalized.count <= Self.maximumQueryCharacters else {
            throw ProductSearchError.queryTooLong(maxCharacters: Self.maximumQueryCharacters)
        }
        guard (1...Self.maximumPageSize).contains(limit) else {
            throw ProductSearchError.invalidPageSize(maximum: Self.maximumPageSize)
        }
        guard offset >= 0 else {
            throw ProductSearchError.invalidOffset
        }
        guard !whitespaceNormalized.isEmpty else {
            return .empty
        }

        // An exact, checksum-valid retail barcode uses the same normalization as
        // scanner/manual lookup before reaching the search repository. This keeps
        // leading-zero, EAN/UPC/GTIN and UPC-E semantics in one reviewed domain
        // implementation. Shorter/invalid numeric input remains a provisional
        // prefix search rather than being rejected as an exact identity.
        let repositoryQuery = (try? Barcode(validating: whitespaceNormalized))?.rawValue
            ?? whitespaceNormalized

        try Task.checkCancellation()
        return try await catalog.search(
            query: repositoryQuery,
            limit: limit,
            offset: offset
        )
    }
}
