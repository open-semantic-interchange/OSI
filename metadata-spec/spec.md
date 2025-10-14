# Semantic Metadata Specification

## Table of Contents
1. [Overview](#overview)
2. [Goal](#goals)
3. [Core Concepts](#core-concepts)

## Overview
This is specification for Open Semantic Interchange (OSI).

## Goals
1. **Standardization**: Establish a uniform language and structure for semantic model definitions, ensuring consistency and ease of interpretation across various tools and systems
2. **Context Preservation**: Maintain contextual information about the metadata itself.
3. **Extensibility**: Support domain-specific extensions while maintaining core compatibility
4. **Interoperability**: Enable exchange and reuse across different AI and BI applications.

## Core Concepts

### Logical Dataset

### Relationships

### Dimensions
Dimensions represent the categorical aspects of data that can be used for grouping and filtering.

```yaml
dimensions:
  - name: string (required)
    expr: string (required) # SQL expression
    description: string (optional)
    ai_context: string (optional) # Additional context for AI tools like synonyms
    extension: string (optional) # Vendor specific attributes
```

### Metrics
Metrics 
```yaml
metrics:
  - name: string (required)
    expr: string (required) # SQL expression, must be an aggregate
    description: string (optional)
    ai_context: string (optional) # Vendor specific attributes
    extension: string (optional) # Vendor specific attributes
```

### Filters

