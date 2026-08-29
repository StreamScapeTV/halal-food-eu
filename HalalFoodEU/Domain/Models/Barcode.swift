import Foundation

struct Barcode: Hashable, Codable, Sendable, Identifiable, CustomStringConvertible {
    enum SymbologyHint: Sendable {
        case ean8
        case upce
        case retail
        case qr
        case code128
        case unknown
    }

    enum ValidationError: LocalizedError, Equatable, Sendable {
        case empty
        case unsupportedCharacters
        case unsupportedLength(Int)
        case invalidCheckDigit
        case unsupportedPayload

        var errorDescription: String? {
            switch self {
            case .empty:
                "Enter or scan a barcode."
            case .unsupportedCharacters:
                "Use a barcode containing only ASCII digits, spaces, or hyphens."
            case let .unsupportedLength(length):
                "A supported retail barcode has 8, 12, 13, or 14 digits; this value has \(length)."
            case .invalidCheckDigit:
                "The barcode check digit is invalid. Check the number and try again."
            case .unsupportedPayload:
                "This QR or Code 128 payload does not contain a supported GS1 GTIN."
            }
        }
    }

    let rawValue: String

    var id: String { rawValue }
    var description: String { rawValue }

    init(validating candidate: String, symbology: SymbologyHint = .unknown) throws {
        let digits = try Self.asciiDigits(from: candidate)

        switch (digits.count, symbology) {
        case (8, .upce):
            rawValue = try Self.normalizeUPCE(digits)
        case (8, _):
            if Self.hasValidGTINCheckDigit(digits) {
                rawValue = String(repeating: "0", count: 6) + digits
            } else if let expanded = try? Self.normalizeUPCE(digits) {
                rawValue = expanded
            } else {
                throw ValidationError.invalidCheckDigit
            }
        case (12, _), (13, _), (14, _):
            guard Self.hasValidGTINCheckDigit(digits) else {
                throw ValidationError.invalidCheckDigit
            }
            rawValue = String(repeating: "0", count: 14 - digits.count) + digits
        default:
            throw ValidationError.unsupportedLength(digits.count)
        }
    }

    private static func asciiDigits(from candidate: String) throws -> String {
        let stripped = candidate.filter { character in
            !character.isWhitespace && character != "-"
        }

        guard !stripped.isEmpty else {
            throw ValidationError.empty
        }

        guard stripped.unicodeScalars.allSatisfy({ scalar in
            scalar.value >= 48 && scalar.value <= 57
        }) else {
            throw ValidationError.unsupportedCharacters
        }

        return stripped
    }

    private static func hasValidGTINCheckDigit(_ digits: String) -> Bool {
        guard digits.count >= 2,
              let expected = digits.last?.wholeNumberValue else {
            return false
        }

        let payload = digits.dropLast().reversed()
        let sum = payload.enumerated().reduce(into: 0) { total, entry in
            let (offset, character) = entry
            guard let value = character.wholeNumberValue else { return }
            total += value * (offset.isMultiple(of: 2) ? 3 : 1)
        }

        return (10 - (sum % 10)) % 10 == expected
    }

    private static func normalizeUPCE(_ digits: String) throws -> String {
        guard digits.count == 8 else {
            throw ValidationError.unsupportedLength(digits.count)
        }

        let values = digits.compactMap(\.wholeNumberValue)
        guard values.count == 8, values[0] == 0 || values[0] == 1 else {
            throw ValidationError.invalidCheckDigit
        }

        let numberSystem = values[0]
        let d1 = values[1]
        let d2 = values[2]
        let d3 = values[3]
        let d4 = values[4]
        let d5 = values[5]
        let d6 = values[6]
        let checkDigit = values[7]

        let expandedPayload: [Int]
        switch d6 {
        case 0, 1, 2:
            expandedPayload = [numberSystem, d1, d2, d6, 0, 0, 0, 0, d3, d4, d5]
        case 3:
            expandedPayload = [numberSystem, d1, d2, d3, 0, 0, 0, 0, 0, d4, d5]
        case 4:
            expandedPayload = [numberSystem, d1, d2, d3, d4, 0, 0, 0, 0, 0, d5]
        default:
            expandedPayload = [numberSystem, d1, d2, d3, d4, d5, 0, 0, 0, 0, d6]
        }

        let upca = expandedPayload.map(String.init).joined() + String(checkDigit)
        guard hasValidGTINCheckDigit(upca) else {
            throw ValidationError.invalidCheckDigit
        }

        return "00" + upca
    }
}

struct BarcodePayloadParser: Sendable {
    func parse(_ payload: String, symbology: Barcode.SymbologyHint = .unknown) throws -> Barcode {
        switch symbology {
        case .qr, .code128:
            if let direct = try? Barcode(validating: payload, symbology: symbology) {
                return direct
            }

            if let gtin = extractDigitalLinkGTIN(from: payload) {
                return try Barcode(validating: gtin, symbology: .qr)
            }

            if let gtin = extractElementStringGTIN(from: payload) {
                return try Barcode(validating: gtin, symbology: .code128)
            }

            throw Barcode.ValidationError.unsupportedPayload
        case .ean8, .upce, .retail, .unknown:
            // Manual and retail payloads should preserve precise validation errors
            // (for example an invalid check digit) instead of becoming a generic
            // unsupported QR/Code 128 message.
            return try Barcode(validating: payload, symbology: symbology)
        }
    }

    private func extractDigitalLinkGTIN(from payload: String) -> String? {
        if let components = URLComponents(string: payload),
           let queryGTIN = components.queryItems?.first(where: { $0.name == "01" })?.value,
           queryGTIN.count == 14 {
            return queryGTIN
        }

        guard let marker = payload.range(of: "/01/") else { return nil }
        let suffix = payload[marker.upperBound...]
        let candidate = String(suffix.prefix(14))
        return candidate.count == 14 ? candidate : nil
    }

    private func extractElementStringGTIN(from payload: String) -> String? {
        if payload.hasPrefix("(01)") {
            let candidate = String(payload.dropFirst(4).prefix(14))
            return candidate.count == 14 ? candidate : nil
        }

        if payload.hasPrefix("]C101") {
            let candidate = String(payload.dropFirst(5).prefix(14))
            return candidate.count == 14 ? candidate : nil
        }

        return nil
    }
}
