import CryptoKit
import Foundation

struct ProductEvidenceSubmissionConfiguration: Equatable, Sendable {
    let destinationEmail: String

    init(destinationEmail: String) throws {
        let trimmed = destinationEmail.trimmingCharacters(in: .whitespacesAndNewlines)
        let parts = trimmed.split(separator: "@", omittingEmptySubsequences: false)
        guard parts.count == 2,
              !parts[0].isEmpty,
              parts[1].contains("."),
              !trimmed.contains(where: { $0.isNewline || $0.isWhitespace }) else {
            throw ProductEvidenceSubmissionError.invalidDestination
        }
        self.destinationEmail = trimmed
    }
}

enum ProductEvidenceSubmissionError: LocalizedError, Equatable, Sendable {
    case invalidDestination
    case invalidSubmissionID
    case invalidMarket
    case invalidIssueType
    case catalogVersionMismatch
    case futureEvidenceDate
    case consentRequired
    case missingRequiredPhoto(ProductEvidencePhotoPurpose)
    case tooManyAttachments
    case attachmentTooLarge
    case totalAttachmentSizeTooLarge
    case invalidAttachment
    case textTooLong(String)
    case packageCreationFailed

    var errorDescription: String? {
        switch self {
        case .invalidDestination:
            String(localized: "The product submission email address is unavailable or invalid.")
        case .invalidSubmissionID:
            String(localized: "The submission identifier is invalid. Start a new submission and try again.")
        case .invalidMarket:
            String(localized: "The product market is invalid.")
        case .invalidIssueType:
            String(localized: "The selected submission issue type does not match this product report.")
        case .catalogVersionMismatch:
            String(localized: "The submission catalog version does not match the bundled catalog.")
        case .futureEvidenceDate:
            String(localized: "The package evidence date cannot be in the future.")
        case .consentRequired:
            String(localized: "Confirm every ownership, privacy, review, and non-guarantee statement before continuing.")
        case let .missingRequiredPhoto(purpose):
            String(localized: "A required package photo is missing: \(purpose.rawValue).")
        case .tooManyAttachments:
            String(localized: "Too many package photos are attached.")
        case .attachmentTooLarge:
            String(localized: "One of the prepared package photos is too large.")
        case .totalAttachmentSizeTooLarge:
            String(localized: "The prepared package photos are too large to send safely as one submission.")
        case .invalidAttachment:
            String(localized: "One of the prepared package photos is invalid.")
        case let .textTooLong(field):
            String(localized: "The \(field) field is too long.")
        case .packageCreationFailed:
            String(localized: "The local submission package could not be prepared.")
        }
    }
}

struct PrepareProductEvidenceSubmission: Sendable {
    static let schemaVersion = 1
    static let sourceType = "user-package-evidence"
    static let consentVersion = "product-evidence-consent-v1"
    static let maximumAttachmentCount = 8
    static let maximumAttachmentBytes = 4_000_000
    static let maximumTotalAttachmentBytes = 18_000_000

    let configuration: ProductEvidenceSubmissionConfiguration
    let appVersion: String
    let catalogVersion: String

    func callAsFunction(
        submissionID: String,
        request: ProductEvidenceSubmissionRequest,
        draft: ProductEvidenceSubmissionDraft,
        attachments: [SanitizedProductEvidenceAttachment],
        now: Date,
        fileManager: FileManager = .default
    ) throws -> PreparedProductEvidenceSubmission {
        guard Self.validSubmissionID(submissionID) else {
            throw ProductEvidenceSubmissionError.invalidSubmissionID
        }
        guard request.market.range(of: #"^[A-Z]{2}$"#, options: .regularExpression) != nil else {
            throw ProductEvidenceSubmissionError.invalidMarket
        }
        guard (request.issueType == .missingProduct) == (draft.issueType == .missingProduct) else {
            throw ProductEvidenceSubmissionError.invalidIssueType
        }
        guard request.catalogVersion == catalogVersion else {
            throw ProductEvidenceSubmissionError.catalogVersionMismatch
        }
        guard draft.observedAt <= now else {
            throw ProductEvidenceSubmissionError.futureEvidenceDate
        }
        guard let consentAcceptedAt = draft.consentAcceptedAt,
              consentAcceptedAt <= now,
              draft.ownsOrMaySubmitPhotos,
              draft.packageEvidenceOnly,
              draft.projectMayReviewAndRedact,
              draft.redistributionRequiresReview,
              draft.noGuaranteedCatalogOrHalalOutcome else {
            throw ProductEvidenceSubmissionError.consentRequired
        }

        try Self.validateText(draft.productName, field: "product name", maxLength: 200)
        try Self.validateText(draft.brand, field: "brand", maxLength: 160)
        try Self.validateText(draft.quantity, field: "quantity", maxLength: 80)
        try Self.validateText(draft.retailer, field: "retailer", maxLength: 120)
        try Self.validateText(draft.city, field: "city", maxLength: 120)
        try Self.validateText(draft.store, field: "store", maxLength: 160)
        try Self.validateText(draft.notes, field: "notes", maxLength: 2_000)

        guard attachments.count <= Self.maximumAttachmentCount else {
            throw ProductEvidenceSubmissionError.tooManyAttachments
        }
        for purpose in draft.issueType.requiredPhotoPurposes where !attachments.contains(where: { $0.purpose == purpose }) {
            throw ProductEvidenceSubmissionError.missingRequiredPhoto(purpose)
        }

        var totalBytes = 0
        var purposeCounters: [ProductEvidencePhotoPurpose: Int] = [:]
        var manifest: [ProductEvidenceAttachmentManifestEntry] = []
        var mailAttachments: [ProductEvidenceMailAttachment] = []
        var filePayloads: [(String, Data)] = []

        for attachment in attachments {
            guard attachment.pixelWidth > 0,
                  attachment.pixelHeight > 0,
                  attachment.pixelWidth <= 2_400,
                  attachment.pixelHeight <= 2_400,
                  attachment.data.count > 0,
                  attachment.sha256.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil else {
                throw ProductEvidenceSubmissionError.invalidAttachment
            }
            let actualDigest = SHA256.hash(data: attachment.data)
                .map { String(format: "%02x", $0) }
                .joined()
            guard actualDigest == attachment.sha256 else {
                throw ProductEvidenceSubmissionError.invalidAttachment
            }
            guard attachment.data.count <= Self.maximumAttachmentBytes else {
                throw ProductEvidenceSubmissionError.attachmentTooLarge
            }
            totalBytes += attachment.data.count
            guard totalBytes <= Self.maximumTotalAttachmentBytes else {
                throw ProductEvidenceSubmissionError.totalAttachmentSizeTooLarge
            }
            let index = (purposeCounters[attachment.purpose] ?? 0) + 1
            purposeCounters[attachment.purpose] = index
            let fileName = "\(attachment.purpose.rawValue)-\(index).jpg"
            manifest.append(
                ProductEvidenceAttachmentManifestEntry(
                    fileName: fileName,
                    purpose: attachment.purpose,
                    mimeType: "image/jpeg",
                    pixelWidth: attachment.pixelWidth,
                    pixelHeight: attachment.pixelHeight,
                    byteSize: attachment.data.count,
                    sha256: attachment.sha256,
                    ownershipState: "user-owned-or-authorized",
                    privacyState: "user-confirmed-package-evidence-only",
                    metadataState: "reencoded-metadata-stripped"
                )
            )
            mailAttachments.append(
                ProductEvidenceMailAttachment(
                    fileName: fileName,
                    mimeType: "image/jpeg",
                    data: attachment.data
                )
            )
            filePayloads.append((fileName, attachment.data))
        }

        let envelope = ProductEvidenceSubmissionEnvelope(
            schemaVersion: Self.schemaVersion,
            sourceType: Self.sourceType,
            submissionID: submissionID,
            appVersion: appVersion,
            catalogVersion: request.catalogVersion,
            gtin: request.barcode.rawValue,
            issueType: draft.issueType,
            market: request.market,
            product: ProductEvidenceProductContext(
                name: Self.nilIfBlank(draft.productName),
                brand: Self.nilIfBlank(draft.brand),
                quantity: Self.nilIfBlank(draft.quantity)
            ),
            retailer: ProductEvidenceRetailerContext(
                retailer: Self.nilIfBlank(draft.retailer),
                city: Self.nilIfBlank(draft.city),
                store: Self.nilIfBlank(draft.store)
            ),
            observedAt: draft.observedAt,
            currentCatalogEvidence: request.currentCatalogEvidence,
            attachments: manifest,
            consent: ProductEvidenceConsent(
                version: Self.consentVersion,
                acceptedAt: consentAcceptedAt,
                ownsOrMaySubmitPhotos: true,
                packageEvidenceOnly: true,
                projectMayReviewAndRedact: true,
                redistributionRequiresReview: true,
                noGuaranteedCatalogOrHalalOutcome: true
            ),
            notes: Self.nilIfBlank(draft.notes)
        )

        let jsonData: Data
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
            encoder.dateEncodingStrategy = .iso8601
            jsonData = try encoder.encode(envelope)
        } catch {
            throw ProductEvidenceSubmissionError.packageCreationFailed
        }
        guard let jsonText = String(data: jsonData, encoding: .utf8) else {
            throw ProductEvidenceSubmissionError.packageCreationFailed
        }

        let temporaryDirectory = fileManager.temporaryDirectory
            .appendingPathComponent("HalalFoodEU-\(submissionID)", isDirectory: true)
        do {
            if fileManager.fileExists(atPath: temporaryDirectory.path) {
                try fileManager.removeItem(at: temporaryDirectory)
            }
            try fileManager.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
            let jsonURL = temporaryDirectory.appendingPathComponent("product-evidence-submission.json")
            try jsonData.write(to: jsonURL, options: .atomic)
            var shareItems = [jsonURL]
            for (fileName, data) in filePayloads {
                let url = temporaryDirectory.appendingPathComponent(fileName)
                try data.write(to: url, options: .atomic)
                shareItems.append(url)
            }

            let subject = "[Halal Food EU Product] \(submissionID) \(request.barcode.rawValue)"
            let body = Self.emailBody(envelope: envelope, jsonText: jsonText)
            let jsonAttachment = ProductEvidenceMailAttachment(
                fileName: "product-evidence-submission.json",
                mimeType: "application/json",
                data: jsonData
            )
            return PreparedProductEvidenceSubmission(
                envelope: envelope,
                destinationEmail: configuration.destinationEmail,
                subject: subject,
                body: body,
                mailAttachments: [jsonAttachment] + mailAttachments,
                shareItems: shareItems,
                temporaryDirectory: temporaryDirectory
            )
        } catch {
            try? fileManager.removeItem(at: temporaryDirectory)
            throw ProductEvidenceSubmissionError.packageCreationFailed
        }
    }

    private static func validSubmissionID(_ value: String) -> Bool {
        value.range(
            of: #"^hfeu-submission-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"#,
            options: .regularExpression
        ) != nil
    }

    private static func validateText(_ value: String, field: String, maxLength: Int) throws {
        guard value.count <= maxLength else {
            throw ProductEvidenceSubmissionError.textTooLong(field)
        }
        if value.unicodeScalars.contains(where: { scalar in
            scalar.value < 0x20 && scalar.value != 0x0A && scalar.value != 0x09
        }) {
            throw ProductEvidenceSubmissionError.textTooLong(field)
        }
    }

    private static func nilIfBlank(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func emailBody(
        envelope: ProductEvidenceSubmissionEnvelope,
        jsonText: String
    ) -> String {
        var lines = [
            "Halal Food EU product evidence submission",
            "",
            "Submission ID: \(envelope.submissionID)",
            "GTIN: \(envelope.gtin)",
            "Issue type: \(envelope.issueType.rawValue)",
            "Market: \(envelope.market)",
            "Catalog version: \(envelope.catalogVersion)",
            "",
            "The attached photos are user-selected package evidence. They must be independently screened and reviewed before any catalog or halal-status change.",
            "Submitting does not guarantee inclusion or a particular halal outcome.",
            "Do not forward or publish sender identity, email headers, faces, receipts, addresses, payment data, credentials, or unrelated personal information into the product catalog.",
            "",
            "--- BEGIN HALAL FOOD EU PRODUCT EVIDENCE JSON ---",
            jsonText,
            "--- END HALAL FOOD EU PRODUCT EVIDENCE JSON ---",
        ]
        if let notes = envelope.notes {
            lines.insert("User notes: \(notes)", at: 7)
        }
        return lines.joined(separator: "\n")
    }
}
