//
//  PlatformIcon.swift
//  newsly
//
//  Created by Assistant on 6/9/25.
//

import SwiftUI

struct PlatformIcon: View {
    let platform: String?
    
    var body: some View {
        Group {
            if let metadata = SourceVisualMetadata.platform(platform) {
                switch metadata.glyph {
                case .text(let value):
                    Text(value)
                        .font(.system(size: 13, weight: .bold))
                        .foregroundColor(metadata.color)
                        .frame(width: 18, height: 18)
                        .background(metadata.color.opacity(0.16))
                        .clipShape(RoundedRectangle(cornerRadius: 3))
                case .system(let name):
                    Image(systemName: name)
                        .foregroundColor(metadata.color)
                }
            }
        }
        .font(.callout)
    }
}

#Preview {
    VStack(spacing: 8) {
        HStack(spacing: 16) {
            VStack { PlatformIcon(platform: "hackernews"); Text("HackerNews").font(.caption2) }
            VStack { PlatformIcon(platform: "reddit"); Text("Reddit").font(.caption2) }
            VStack { PlatformIcon(platform: "substack"); Text("Substack").font(.caption2) }
        }
        HStack(spacing: 16) {
            VStack { PlatformIcon(platform: "podcast"); Text("Podcast").font(.caption2) }
            VStack { PlatformIcon(platform: "twitter"); Text("Twitter").font(.caption2) }
            VStack { PlatformIcon(platform: "unknown"); Text("Unknown").font(.caption2) }
        }
    }
    .padding()
}
