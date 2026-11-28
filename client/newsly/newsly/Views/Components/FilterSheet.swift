//
//  FilterSheet.swift
//  newsly
//
//  Created by Assistant on 7/9/25.
//

import SwiftUI

struct FilterSheet: View {
    @Binding var selectedContentType: String
    @Binding var selectedDate: String
    @Environment(\.dismiss) private var dismiss
    
    let contentTypes: [String]
    let availableDates: [String]
    
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Form {
                    // Content Type Section
                    Section(header: Text("Content Type").font(.modalSupporting)) {
                        Picker("Content Type", selection: $selectedContentType) {
                            Text("All Types").tag("all")
                            ForEach(contentTypes, id: \.self) { type in
                                Text(type.replacingOccurrences(of: "_", with: " ").capitalized)
                                    .tag(type)
                            }
                        }
                        .pickerStyle(InlinePickerStyle())
                        .labelsHidden()
                    }
                    
                    // Date Section
                    Section(header: Text("Date").font(.modalSupporting)) {
                        Menu {
                            Button("All Dates") {
                                selectedDate = ""
                            }
                            ForEach(availableDates, id: \.self) { date in
                                Button(formatDate(date)) {
                                    selectedDate = date
                                }
                            }
                        } label: {
                            HStack {
                                Text(selectedDateTitle)
                                    .foregroundStyle(Color.onSurface)
                                Spacer()
                                Image(systemName: "chevron.up.chevron.down")
                                    .font(.appSymbol(size: 13, weight: .semibold))
                                    .foregroundStyle(Color.onSurfaceSecondary)
                                    .accessibilityHidden(true)
                            }
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        }
                        .accessibilityLabel("Date")
                        .accessibilityValue(selectedDateTitle)
                    }
                    
                }
                .font(.modalBody)
            }
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }
    
    private static let inputDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private static let displayDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .none
        return formatter
    }()

    private func formatDate(_ dateString: String) -> String {
        guard let date = Self.inputDateFormatter.date(from: dateString) else {
            return dateString
        }
        return Self.displayDateFormatter.string(from: date)
    }

    private var selectedDateTitle: String {
        selectedDate.isEmpty ? "All Dates" : formatDate(selectedDate)
    }
}
