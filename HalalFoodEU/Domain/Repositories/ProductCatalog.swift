import Foundation

protocol ProductCatalog: Sendable {
    func product(for barcode: Barcode) async throws -> ProductRecord?
}

enum ProductCatalogError: LocalizedError, Sendable {
    case unavailable(String)
    case incompatibleSchema(expected: Int, actual: Int)
    case invalidRecord(String)
    case queryFailed(String)

    var errorDescription: String? {
        switch self {
        case let .unavailable(message):
            "The offline product catalog is unavailable: \(message)"
        case let .incompatibleSchema(expected, actual):
            "The bundled catalog schema is \(actual), but this app supports schema \(expected)."
        case let .invalidRecord(message):
            "The catalog contains an invalid product record: \(message)"
        case let .queryFailed(message):
            "The offline catalog lookup failed: \(message)"
        }
    }
}
