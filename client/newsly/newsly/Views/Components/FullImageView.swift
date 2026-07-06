//
//  FullImageView.swift
//  newsly
//
//  Created by Assistant on 12/20/25.
//

import SwiftUI

struct FullImageView: View {
    let imageURL: URL
    let thumbnailURL: URL?
    @Environment(\.dismiss) private var dismiss
    @State private var scale: CGFloat = 1.0
    @State private var lastScale: CGFloat = 1.0

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                Color.black.ignoresSafeArea()

                CachedAsyncImage(
                    url: imageURL,
                    thumbnailUrl: thumbnailURL,
                    targetSize: proxy.size
                ) { image in
                    image
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .scaleEffect(scale)
                        .gesture(
                            MagnificationGesture()
                                .onChanged { value in
                                    scale = lastScale * value
                                }
                                .onEnded { _ in
                                    lastScale = scale
                                    // Snap back if too small
                                    if scale < 1.0 {
                                        withAnimation(AppMotion.press) {
                                            scale = 1.0
                                            lastScale = 1.0
                                        }
                                    }
                                }
                        )
                        .onTapGesture(count: 2) {
                            withAnimation(AppMotion.press) {
                                if scale > 1.0 {
                                    scale = 1.0
                                    lastScale = 1.0
                                } else {
                                    scale = 2.0
                                    lastScale = 2.0
                                }
                            }
                        }
                } placeholder: {
                    ProgressView()
                        .tint(.white)
                }

                // Close button
                VStack {
                    HStack {
                        Spacer()
                        Button {
                            dismiss()
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.appTitle)
                                .foregroundColor(.white.opacity(0.8))
                                .padding()
                        }
                    }
                    Spacer()
                }
            }
            .onTapGesture {
                guard scale == 1.0 else { return }
                dismiss()
            }
        }
    }
}
