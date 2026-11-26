//
//  FoldDiagnosticsOverlay.swift
//  newsly
//

#if DEBUG
import os.log
import SwiftUI

private let foldLogger = Logger(subsystem: "com.newsly", category: "FoldDiagnostics")

/// DEBUG-only Phase 0 instrument for foldable devices. Launch with
/// `-newslyDebugFoldOverlay YES` to outline the system's fold (division) and
/// occlusion regions, print their frames and the hinge state, and log each
/// change. Draws nothing and never takes touches otherwise.
struct FoldDiagnosticsOverlay: View {
    static var isEnabled: Bool {
        UserDefaults.standard.bool(forKey: "newslyDebugFoldOverlay")
    }

    var body: some View {
        if Self.isEnabled {
            if #available(iOS 27.1, *) {
                FoldDiagnosticsCanvas()
                    .ignoresSafeArea()
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
    }
}

@available(iOS 27.1, *)
private struct FoldDiagnosticsCanvas: View {
    @State private var hingeDescription = "hinge: unknown"

    var body: some View {
        GeometryReader { proxy in
            let divisions = proxy.reservedRegions(kind: .division, options: .includeInactive)
            let occlusions = proxy.reservedRegions(kind: .occlusion, options: .includeInactive)
            ZStack(alignment: .topLeading) {
                ForEach(divisions) { region in
                    regionShape(region, color: .red)
                }
                ForEach(occlusions) { region in
                    regionShape(region, color: .orange)
                }
                summary(size: proxy.size, divisions: divisions, occlusions: occlusions)
            }
            .onAppear { log(size: proxy.size, divisions: divisions, occlusions: occlusions) }
            .onChange(of: divisions) { _, new in log(size: proxy.size, divisions: new, occlusions: occlusions) }
            .onChange(of: proxy.size) { _, new in log(size: new, divisions: divisions, occlusions: occlusions) }
        }
        .onHingeChange { _, context in
            guard let hinge = context.hinge else {
                hingeDescription = "hinge: none"
                return
            }
            let status = switch hinge.status {
            case .closed: "closed"
            case .partiallyOpen: "partiallyOpen"
            case .fullyOpen: "fullyOpen"
            default: "other"
            }
            hingeDescription = "hinge: \(status) \(Int(hinge.angle.degrees))°"
            foldLogger.info("[Fold] \(hingeDescription, privacy: .public)")
        }
    }

    private func regionShape(_ region: ReservedRegion, color: Color) -> some View {
        Rectangle()
            .fill(color.opacity(region.isActive ? 0.22 : 0.08))
            .overlay(
                Rectangle().strokeBorder(color, style: StrokeStyle(lineWidth: 1.5, dash: [6, 4]))
            )
            .frame(width: max(region.frame.width, 2), height: max(region.frame.height, 2))
            .offset(x: region.frame.minX, y: region.frame.minY)
    }

    private func summary(size: CGSize, divisions: [ReservedRegion], occlusions: [ReservedRegion]) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("window \(Int(size.width))×\(Int(size.height)) pt")
            Text(hingeDescription)
            ForEach(divisions) { Text("fold \(describe($0))") }
            ForEach(occlusions) { Text("occl \(describe($0))") }
        }
        .font(.system(size: 9, weight: .semibold, design: .monospaced))
        .foregroundStyle(.white)
        .padding(5)
        .background(Color.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 5))
        .padding(.leading, 60)
        .padding(.top, 60)
    }

    private func describe(_ region: ReservedRegion) -> String {
        let f = region.frame
        let m = region.margins
        return "x\(Int(f.minX)) y\(Int(f.minY)) \(Int(f.width))×\(Int(f.height)) m[l\(Int(m.leading)) r\(Int(m.trailing)) t\(Int(m.top)) b\(Int(m.bottom))]\(region.isActive ? "" : " inactive")"
    }

    private func log(size: CGSize, divisions: [ReservedRegion], occlusions: [ReservedRegion]) {
        let folds = divisions.map(describe).joined(separator: "; ")
        let occl = occlusions.map(describe).joined(separator: "; ")
        foldLogger.info(
            "[Fold] window=\(Int(size.width), privacy: .public)x\(Int(size.height), privacy: .public) divisions=[\(folds, privacy: .public)] occlusions=[\(occl, privacy: .public)]"
        )
    }
}
#endif
