//
//  FilterBar.swift
//  newsly
//
//  Created by Assistant on 7/8/25.
//

import SwiftUI

struct FilterBar: View {
    @Binding var selectedContentType: String
    @Binding var selectedDate: String
    @Binding var selectedReadFilter: String
    
    let contentTypes: [String]
    let availableDates: [String]
    let onFilterChange: () async -> Void
    
    var body: some View {
        VStack(spacing: 12) {
            // Content Type Filter
            VStack(alignment: .leading, spacing: 4) {
                Text("Content Type")
                    .font(.caption)
                    .foregroundColor(.secondary)
                
                Picker("Content Type", selection: $selectedContentType) {
                    Text("All").tag("all")
                    ForEach(contentTypes, id: \.self) { type in
                        Text(type.replacingOccurrences(of: "_", with: " ").capitalized)
                            .tag(type)
                    }
                }
                .pickerStyle(SegmentedPickerStyle())
                .onChange(of: selectedContentType) { _, _ in
                    Task { await onFilterChange() }
                }
            }
            
            HStack(spacing: 12) {
                // Date Filter
                VStack(alignment: .leading, spacing: 4) {
                    Text("Date")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    
                    Picker("Date", selection: $selectedDate) {
                        Text("All Dates").tag("")
                        ForEach(availableDates, id: \.self) { date in
                            Text(date).tag(date)
                        }
                    }
                    .pickerStyle(MenuPickerStyle())
                    .frame(maxWidth: .infinity)
                    .onChange(of: selectedDate) { _, _ in
                        Task { await onFilterChange() }
                    }
                }
                
                // Read Status Filter
                VStack(alignment: .leading, spacing: 4) {
                    Text("Read Status")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    
                    Picker("Read Status", selection: $selectedReadFilter) {
                        Text("Unread Only").tag("unread")
                        Text("All Content").tag("all")
                        Text("Read Only").tag("read")
                    }
                    .pickerStyle(MenuPickerStyle())
                    .frame(maxWidth: .infinity)
                    .onChange(of: selectedReadFilter) { _, _ in
                        Task { await onFilterChange() }
                    }
                }
            }
        }
        .padding()
        .background(Color.surfaceSecondary)
        .cornerRadius(12)
    }
}
