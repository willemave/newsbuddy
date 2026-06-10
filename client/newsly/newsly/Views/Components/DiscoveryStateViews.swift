//
//  DiscoveryStateViews.swift
//  newsly
//

import SwiftUI

// MARK: - Loading State (Skeleton)

struct DiscoveryLoadingStateView: View {
    @State private var shimmer = false

    var body: some View {
        VStack(spacing: 12) {
            ForEach(0..<3, id: \.self) { _ in
                skeletonCard
            }
        }
        .padding(.horizontal, Spacing.appHorizontalMargin)
        .padding(.top, 32)
        .padding(.bottom, 200)
        .onAppear {
            withAnimation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true)) {
                shimmer = true
            }
        }
    }

    private var skeletonCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            // Headline skeleton
            RoundedRectangle(cornerRadius: 4)
                .fill(Color.surfaceTertiary)
                .frame(height: 18)
                .frame(maxWidth: .infinity)
                .opacity(shimmer ? 0.4 : 0.8)

            RoundedRectangle(cornerRadius: 4)
                .fill(Color.surfaceTertiary)
                .frame(width: 200, height: 18)
                .opacity(shimmer ? 0.3 : 0.7)

            // Metadata bar skeleton
            HStack(spacing: 6) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(Color.surfaceTertiary)
                    .frame(width: 10, height: 10)
                    .opacity(shimmer ? 0.4 : 0.7)

                RoundedRectangle(cornerRadius: 2)
                    .fill(Color.surfaceTertiary)
                    .frame(width: 50, height: 8)
                    .opacity(shimmer ? 0.3 : 0.6)

                RoundedRectangle(cornerRadius: 2)
                    .fill(Color.surfaceTertiary)
                    .frame(width: 80, height: 8)
                    .opacity(shimmer ? 0.3 : 0.6)

                Spacer()
            }
        }
        .padding(16)
        .background(Color.surfaceSecondary)
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.editorialBorder, lineWidth: 1)
        )
        .cornerRadius(12)
    }
}

// MARK: - Error State

struct DiscoveryErrorStateView: View {
    let error: String
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.appSymbol(size: 28, weight: .light))
                .foregroundColor(Color.onSurfaceSecondary)

            VStack(spacing: 6) {
                Text("Something went wrong")
                    .font(.listTitle.weight(.semibold))
                    .foregroundColor(Color.onSurface)

                Text(error)
                    .font(.listSubtitle)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }

            Button(action: onRetry) {
                Label("Try Again", systemImage: "arrow.clockwise")
                    .font(.listSubtitle.weight(.medium))
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
            .padding(.top, 4)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 100)
        .padding(.bottom, 200)
    }
}

// MARK: - Processing State

struct DiscoveryProcessingStateView: View {
    let runStatusDescription: String
    let currentJobStage: Int

    @State private var pulseScale: CGFloat = 1.0

    var body: some View {
        VStack(spacing: 24) {
            Image(systemName: "sparkles")
                .font(.appSymbol(size: 32, weight: .light))
                .foregroundColor(Color.onSurfaceSecondary)

            VStack(spacing: 8) {
                Text("Discovering New Content")
                    .font(.listTitle.weight(.semibold))
                    .foregroundColor(Color.onSurface)

                Text(runStatusDescription)
                    .font(.listSubtitle)
                    .foregroundColor(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 40)
            }

            // Progress dots
            HStack(spacing: 8) {
                ForEach(0..<4, id: \.self) { index in
                    Circle()
                        .fill(index <= currentJobStage ? Color.onSurface : Color.editorialBorder)
                        .frame(width: 6, height: 6)
                        .scaleEffect(index == currentJobStage ? pulseScale : 1.0)
                }
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 100)
        .padding(.bottom, 200)
        .onAppear {
            withAnimation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true)) {
                pulseScale = 1.6
            }
        }
    }
}

// MARK: - Empty State

struct DiscoveryEmptyStateView: View {
    let onGenerate: () -> Void

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "sparkles")
                .font(.appSymbol(size: 48, weight: .light))
                .foregroundStyle(Color.onSurfaceTertiary.opacity(0.72))

            VStack(spacing: 6) {
                Text("Discover New Content")
                    .font(.listTitle.weight(.semibold))
                    .foregroundStyle(Color.onSurface)

                Text("Get personalized suggestions for feeds, podcasts, and channels based on your reading history.")
                    .font(.listSubtitle)
                    .foregroundStyle(Color.onSurfaceSecondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 280)
            }

            Button(action: onGenerate) {
                Label("Generate Suggestions", systemImage: "sparkles")
                    .font(.listSubtitle.weight(.medium))
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.regular)
            .padding(.top, 4)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.surfacePrimary)
    }
}
