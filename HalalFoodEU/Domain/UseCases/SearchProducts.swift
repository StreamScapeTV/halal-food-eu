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
        let normalizedQuery = query
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")

        guard normalizedQuery.count <= Self.maximumQueryCharacters else {
            throw ProductSearchError.queryTooLong(maxCharacters: Self.maximumQueryCharacters)
        }
        guard (1...Self.maximumPageSize).contains(limit) else {
            throw ProductSearchError.invalidPageSize(maximum: Self.maximumPageSize)
        }
        guard offset >= 0 else {
            throw ProductSearchError.invalidOffset
        }
        guard !normalizedQuery.isEmpty else {
            return .empty
        }

        try Task.checkCancellation()
        return try await catalog.search(
            query: normalizedQuery,
            limit: limit,
            offset: offset
        )
    }
}
