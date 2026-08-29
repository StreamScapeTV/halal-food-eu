#!/usr/bin/env ruby
# frozen_string_literal: true

require "yaml"

root = File.expand_path("..", __dir__)
paths = Dir[File.join(root, ".github", "ISSUE_TEMPLATE", "*.yml")].sort
raise "no issue forms found" if paths.empty?

paths.each do |path|
  data = YAML.safe_load(File.read(path), aliases: true)
  raise "#{path}: root must be a mapping" unless data.is_a?(Hash)

  if File.basename(path) == "config.yml"
    raise "#{path}: blank_issues_enabled must be false" unless data["blank_issues_enabled"] == false
    next
  end

  %w[name description body].each do |key|
    value = data[key]
    raise "#{path}: missing #{key}" if value.nil? || (value.respond_to?(:empty?) && value.empty?)
  end
  raise "#{path}: body must be an array" unless data["body"].is_a?(Array)

  labels = Array(data["labels"])
  priorities = labels.grep(/^priority:/)
  statuses = labels.grep(/^status:/)
  raise "#{path}: form must apply exactly one default priority label" unless priorities.length == 1
  raise "#{path}: form must apply exactly one default status label" unless statuses.length == 1

  ids = []
  data["body"].each_with_index do |entry, index|
    raise "#{path}: body[#{index}] must be a mapping" unless entry.is_a?(Hash)
    type = entry["type"]
    raise "#{path}: body[#{index}] has no type" if type.to_s.empty?
    next if type == "markdown"

    id = entry["id"]
    raise "#{path}: body[#{index}] has no id" if id.to_s.empty?
    raise "#{path}: duplicate id #{id}" if ids.include?(id)
    ids << id
  end

  normalized_text = File.read(path).downcase.gsub(/[*_`]/, "")
  prohibition = normalized_text.include?("do not") || normalized_text.include?("never")
  sensitive_term = ["credential", "secret", "api key", "password", "token"].any? do |term|
    normalized_text.include?(term)
  end
  unless prohibition && sensitive_term
    raise "#{path}: must contain an explicit public-issue secret/credential warning"
  end
end

puts "Validated #{paths.length} issue template files"
