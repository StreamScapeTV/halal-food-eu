import Foundation

struct AdditiveReferenceCatalog: Decodable, Equatable, Sendable {
    let schemaVersion: Int
    let datasetVersion: String
    let referenceRevision: String
    let reviewedAt: String
    let nextReviewAt: String
    let identityOnly: Bool
    let languages: [String]
    let source: AdditiveReferenceSource
    let entries: [AdditiveReferenceEntry]

    func validateForRuntime() throws {
        guard schemaVersion == 1 else {
            throw AdditiveReferenceCatalogError.unsupportedSchema
        }
        guard identityOnly else {
            throw AdditiveReferenceCatalogError.notIdentityOnly
        }
        guard !datasetVersion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !referenceRevision.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !entries.isEmpty,
              source.sourceKey == "eu-additives",
              source.jurisdiction == "EU" else {
            throw AdditiveReferenceCatalogError.invalidReference
        }

        let ids = entries.map(\.id)
        guard Set(ids).count == ids.count,
              entries.allSatisfy({ $0.id.first == "E" && $0.id.dropFirst().contains(where: \.isNumber) }) else {
            throw AdditiveReferenceCatalogError.invalidReference
        }
    }

    func matches(in ingredientText: String, languageCode: String) -> [AdditiveReferenceMatch] {
        guard identityOnly, !ingredientText.isEmpty else { return [] }

        let language = languageCode
            .split(separator: "-", maxSplits: 1)
            .first
            .map { String($0).lowercased() } ?? languageCode.lowercased()
        let normalized = Self.normalizeWithOffsets(ingredientText)
        var matches: [AdditiveReferenceMatch] = []

        for entry in entries.sorted(by: { $0.id < $1.id }) where entry.status != .removed {
            var sourceSpan = Self.additiveIDMatch(entry.id, in: ingredientText)

            if sourceSpan == nil, languages.contains(language) {
                let names = (entry.officialNames[language] ?? []) + (entry.aliases[language] ?? [])
                sourceSpan = names.lazy.compactMap { name in
                    Self.nameMatch(
                        Self.normalized(name),
                        in: normalized.text,
                        offsets: normalized.offsets,
                        sourceText: ingredientText
                    )
                }.first
            }

            guard let sourceSpan else { continue }
            let displayName = entry.officialNames[language]?.first
                ?? entry.officialNames["en"]?.first
                ?? entry.officialNames.values.compactMap(\.first).first
                ?? entry.id

            matches.append(
                AdditiveReferenceMatch(
                    additiveID: entry.id,
                    displayName: displayName,
                    sourceSpan: sourceSpan,
                    technologicalFunctions: entry.technologicalFunctions,
                    originPossibilities: entry.originPossibilities,
                    legalReferences: entry.legalReferences,
                    referenceRevision: referenceRevision
                )
            )
        }

        return matches
    }

    private static func additiveIDMatch(_ additiveID: String, in text: String) -> AdditiveSourceSpan? {
        guard additiveID.first == "E" else { return nil }
        var body = String(additiveID.dropFirst())
        var roman: String?
        if let open = body.firstIndex(of: "("), let close = body.lastIndex(of: ")"), open < close {
            roman = String(body[body.index(after: open)..<close])
            body = String(body[..<open])
        }

        var suffix: Character?
        if let last = body.last, last.isLetter {
            suffix = last
            body.removeLast()
        }
        guard !body.isEmpty, body.allSatisfy(\.isNumber) else { return nil }

        var pattern = #"(?<![A-Za-z0-9])[Ee]\s*"#
        pattern += body.map { NSRegularExpression.escapedPattern(for: String($0)) }.joined(separator: #"\s*"#)
        if let suffix {
            pattern += #"\s*"# + NSRegularExpression.escapedPattern(for: String(suffix))
        }
        if let roman {
            pattern += #"\s*\(\s*"#
            pattern += roman.map { NSRegularExpression.escapedPattern(for: String($0)) }.joined(separator: #"\s*"#)
            pattern += #"\s*\)"#
        }
        pattern += #"(?![A-Za-z0-9])"#

        guard let expression = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else {
            return nil
        }
        let fullRange = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let result = expression.firstMatch(in: text, range: fullRange),
              let range = Range(result.range, in: text) else {
            return nil
        }
        return AdditiveSourceSpan(
            startUTF16: result.range.location,
            endUTF16: result.range.location + result.range.length,
            text: String(text[range])
        )
    }

    private static func nameMatch(
        _ needle: String,
        in haystack: String,
        offsets: [String.Index],
        sourceText: String
    ) -> AdditiveSourceSpan? {
        guard !needle.isEmpty, !haystack.isEmpty else { return nil }
        var searchStart = haystack.startIndex

        while searchStart < haystack.endIndex,
              let range = haystack.range(of: needle, range: searchStart..<haystack.endIndex) {
            let beforeBoundary = range.lowerBound == haystack.startIndex
                || haystack[haystack.index(before: range.lowerBound)] == " "
            let afterBoundary = range.upperBound == haystack.endIndex
                || haystack[range.upperBound] == " "

            if beforeBoundary, afterBoundary {
                let startOffset = haystack.distance(from: haystack.startIndex, to: range.lowerBound)
                let endOffset = haystack.distance(from: haystack.startIndex, to: range.upperBound)
                guard startOffset >= 0, endOffset > startOffset, endOffset <= offsets.count else {
                    return nil
                }
                let sourceStart = offsets[startOffset]
                let lastSourceIndex = offsets[endOffset - 1]
                let sourceEnd = sourceText.index(after: lastSourceIndex)
                let sourceRange = sourceStart..<sourceEnd
                let utf16Range = NSRange(sourceRange, in: sourceText)
                return AdditiveSourceSpan(
                    startUTF16: utf16Range.location,
                    endUTF16: utf16Range.location + utf16Range.length,
                    text: String(sourceText[sourceRange])
                )
            }

            searchStart = range.upperBound
        }
        return nil
    }

    private static func normalized(_ value: String) -> String {
        normalizeWithOffsets(value).text
    }

    private static func normalizeWithOffsets(_ value: String) -> (text: String, offsets: [String.Index]) {
        var normalized = ""
        var offsets: [String.Index] = []
        var previousWasSeparator = true

        for sourceIndex in value.indices {
            let character = String(value[sourceIndex])
            let folded = character.folding(
                options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive],
                locale: Locale(identifier: "en_US_POSIX")
            )

            for scalar in folded.unicodeScalars {
                if CharacterSet.alphanumerics.contains(scalar) {
                    for outputCharacter in String(scalar) {
                        normalized.append(outputCharacter)
                        offsets.append(sourceIndex)
                    }
                    previousWasSeparator = false
                } else if !previousWasSeparator, !normalized.isEmpty {
                    normalized.append(" ")
                    offsets.append(sourceIndex)
                    previousWasSeparator = true
                }
            }
        }

        if normalized.last == " " {
            normalized.removeLast()
            offsets.removeLast()
        }
        return (normalized, offsets)
    }
}

struct AdditiveReferenceSource: Decodable, Equatable, Sendable {
    let sourceKey: String
    let jurisdiction: String
    let acquisitionMethod: String
    let unionListCELEX: String
    let unionListELI: String
    let specificationsCELEX: String
    let specificationsELI: String
    let commissionReference: String
    let efsaReference: String
    let licenseIdentifier: String
    let attribution: String
    let legalEffectLimitation: String
}

struct AdditiveReferenceEntry: Decodable, Equatable, Sendable {
    enum Status: String, Decodable, Equatable, Sendable {
        case active
        case changed
        case removed
    }

    let id: String
    let status: Status
    let officialNames: [String: [String]]
    let aliases: [String: [String]]
    let technologicalFunctions: [String]
    let originPossibilities: [AdditiveOriginPossibility]
    let legalReferences: [AdditiveLegalReference]
    let reviewedAt: String
}

struct AdditiveOriginPossibility: Decodable, Equatable, Sendable, Identifiable {
    let kind: String
    let statement: String
    let reference: String

    var id: String { "\(kind)|\(reference)|\(statement)" }
}

struct AdditiveLegalReference: Decodable, Equatable, Sendable, Identifiable {
    let kind: String
    let reference: String
    let revision: String

    var id: String { "\(kind)|\(reference)|\(revision)" }
}

struct AdditiveSourceSpan: Equatable, Sendable {
    let startUTF16: Int
    let endUTF16: Int
    let text: String
}

struct AdditiveReferenceMatch: Equatable, Sendable, Identifiable {
    let additiveID: String
    let displayName: String
    let sourceSpan: AdditiveSourceSpan
    let technologicalFunctions: [String]
    let originPossibilities: [AdditiveOriginPossibility]
    let legalReferences: [AdditiveLegalReference]
    let referenceRevision: String

    var id: String { additiveID }

    var primaryReference: String? {
        originPossibilities.first?.reference
            ?? legalReferences.first(where: { $0.kind == "specification" })?.reference
            ?? legalReferences.first?.reference
    }
}

enum AdditiveReferenceCatalogError: Error, LocalizedError, Sendable {
    case missingResource
    case unsupportedSchema
    case notIdentityOnly
    case invalidReference

    var errorDescription: String? {
        switch self {
        case .missingResource:
            "The bundled EU additive reference is missing."
        case .unsupportedSchema:
            "The bundled EU additive reference schema is unsupported."
        case .notIdentityOnly:
            "The bundled EU additive reference attempted to encode a conclusion instead of identity-only data."
        case .invalidReference:
            "The bundled EU additive reference is invalid."
        }
    }
}
