import Foundation
import SwiftUI

struct AdditiveReferenceSection: View {
    let product: ProductRecord
    let catalog: AdditiveReferenceCatalog?

    private var text: AdditiveReferenceText { AdditiveReferenceText() }

    private var matches: [AdditiveReferenceMatch] {
        guard let catalog, let observation = product.observation else { return [] }
        return catalog.matches(
            in: observation.text,
            languageCode: observation.languageCode
        )
    }

    @ViewBuilder
    var body: some View {
        if let catalog, !matches.isEmpty {
            Section {
                LabeledContent(text.string("field.referenceRevision"), value: catalog.referenceRevision)

                ForEach(matches) { match in
                    VStack(alignment: .leading, spacing: 6) {
                        Label(
                            "\(match.additiveID) — \(match.displayName)",
                            systemImage: "list.bullet.rectangle"
                        )
                        .font(.headline)

                        Text(text.format("additive.matchedAs", match.sourceSpan.text))
                            .font(.footnote)
                            .foregroundStyle(.secondary)

                        if !match.technologicalFunctions.isEmpty {
                            Text(
                                text.format(
                                    "additive.functions",
                                    match.technologicalFunctions.joined(separator: ", ")
                                )
                            )
                            .font(.footnote)
                        }

                        if match.originPossibilities.isEmpty {
                            Text(text.string("additive.identityOnly"))
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        } else {
                            Text(text.string("additive.originContext"))
                                .font(.subheadline.bold())
                            ForEach(match.originPossibilities) { origin in
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(origin.statement)
                                        .font(.footnote)
                                    sourceLink(origin.reference)
                                        .font(.caption)
                                }
                            }
                        }

                        if match.originPossibilities.isEmpty,
                           let reference = match.primaryReference {
                            sourceLink(reference)
                                .font(.caption)
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
            } header: {
                Text(text.string("section.additives"))
            } footer: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(text.string("additive.footer"))
                    Text(
                        text.format(
                            "additive.source",
                            catalog.source.attribution,
                            catalog.source.licenseIdentifier
                        )
                    )
                }
            }
        }
    }

    @ViewBuilder
    private func sourceLink(_ reference: String) -> some View {
        if let url = URL(string: reference),
           url.scheme?.lowercased() == "https",
           url.host != nil {
            Link(destination: url) {
                Label(text.string("action.openReference"), systemImage: "arrow.up.right.square")
            }
        }
    }
}

struct AdditiveReferenceText {
    let bundle: Bundle
    let locale: Locale

    init(bundle: Bundle = .main, locale: Locale = .current) {
        self.bundle = bundle
        self.locale = locale
    }

    func string(_ key: String) -> String {
        bundle.localizedString(forKey: key, value: key, table: "AdditiveReference")
    }

    func format(_ key: String, _ arguments: CVarArg...) -> String {
        String(format: string(key), locale: locale, arguments: arguments)
    }
}
