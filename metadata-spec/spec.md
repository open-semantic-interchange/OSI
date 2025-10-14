# Semantic Metadata Specification

## Table of Contents
1. [Overview](#overview)
2. [Goal](#goals)
3. [Core Concepts](#core-concepts)

## Overview
This is the specification for Open Semantic Interchange (OSI).

## Goals
1. **Standardization**: Establish a uniform language and structure for semantic model definitions, ensuring consistency and ease of interpretation across various tools and systems
2. **Context Preservation**: Maintain contextual information about the metadata itself.
3. **Extensibility**: Support domain-specific extensions while maintaining core compatibility
4. **Interoperability**: Enable exchange and reuse across different AI and BI applications.

## Core Concepts
### Semantic Model
A semantic model is an representation of business data that organizes it into logical tables and defines relationships between them. Logical tables represent business entities or concepts. The complete spec for semantic model is below:
```yaml
semantic_model:
  - name: string (required)
    description: string (optional)
    logical_datasets: # See more information in [Logical Dataset](#logical-dataset)
    relationships: # See more information in [Relationship](#relationships)
    ai_context: string (optional) # Additional context for AI tools like custom prompts
    extension: string (optional) # Vendor specific attributes
```
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
Metrics are quantitative measures defined on business data, representing key calculations like sums, averages, or ratios. 
```yaml
metrics:
  - name: string (required)
    expr: string (required) # SQL expression, must be an aggregate
    description: string (optional)
    ai_context: string (optional) # Additional context for AI tools like synonyms
    extension: string (optional) # Vendor specific attributes
```

### Filters
Filters associated with the logical dataset to allow query results to be limited to specific data subsets.
```yaml
filters:
  - name: string (required)
    expr: string (required) # SQL expression for the filter
    description: string (optional)
    ai_context: string (optional) # Additional context for AI tools like synonyms
    extension: string (optional) # Vendor specific attributes
```
