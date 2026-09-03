import Foundation

enum ProductSearchMatchKind: String, Codable, Equatable, Sendable {
    case barcodeExact
    case barcodePrefix
    case text
}

struct ProductSearchResult: Identifiable, Equatable, Sendable {
    let barcode: Barcode
    let name: String
    let brand: String?
    let quantity: String?
    let matchKind: ProductSearchMatchKind

    var id: String { barcode.rawValue }
}

struct ProductSearchPage: Equatable, Sendable {
    let results: [ProductSearchResult]
    let offset: Int
    let hasMore: Bool

    static let empty = ProductSearchPage(results: [], offset: 0, hasMore: false)
}

enum ProductSearchError: LocalizedError, Equatable, Sendable {
    case queryTooLong(maxCharacters: Int)
    case invalidPageSize(maximum: Int)
    case invalidOffset

    var errorDescription: String? {
        switch self {
        case let .queryTooLong(maxCharacters):
            "Search text is too long. Use at most \(maxCharacters) characters."
        case let .invalidPageSize(maximum):
            "Product search pages are limited to \(maximum) results."
        case .invalidOffset:
            "The product search page offset is invalid."
        }
    }
}
