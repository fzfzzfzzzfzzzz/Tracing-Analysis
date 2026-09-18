---
dataset_info:
  features:
  - name: instance_id
    dtype: string
  - name: run_id
    dtype: string
  - name: resolved
    dtype: bool
  - name: messages
    list:
    - name: content
      dtype: string
    - name: function_call
      dtype: 'null'
    - name: name
      dtype: string
    - name: role
      dtype: string
    - name: tool_call_id
      dtype: string
    - name: tool_calls
      list:
      - name: function
        struct:
        - name: arguments
          dtype: string
        - name: name
          dtype: string
      - name: id
        dtype: string
      - name: index
        dtype: int64
      - name: type
        dtype: string
  - name: tools
    list:
    - name: function
      struct:
      - name: description
        dtype: string
      - name: name
        dtype: string
      - name: parameters
        struct:
        - name: properties
          struct:
          - name: command
            struct:
            - name: description
              dtype: string
            - name: enum
              sequence: string
            - name: type
              dtype: string
          - name: file_text
            struct:
            - name: description
              dtype: string
            - name: type
              dtype: string
          - name: insert_line
            struct:
            - name: description
              dtype: string
            - name: type
              dtype: string
          - name: new_str
            struct:
            - name: description
              dtype: string
            - name: type
              dtype: string
          - name: old_str
            struct:
            - name: description
              dtype: string
            - name: type
              dtype: string
          - name: path
            struct:
            - name: description
              dtype: string
            - name: type
              dtype: string
          - name: view_range
            struct:
            - name: description
              dtype: string
            - name: items
              struct:
              - name: type
                dtype: string
            - name: type
              dtype: string
        - name: required
          sequence: string
        - name: type
          dtype: string
    - name: type
      dtype: string
  - name: test_result
    struct:
    - name: apply_patch_output
      dtype: string
    - name: git_patch
      dtype: string
    - name: report
      struct:
      - name: empty_generation
        dtype: bool
      - name: error_eval
        dtype: bool
      - name: failed_apply_patch
        dtype: bool
      - name: resolved
        dtype: bool
      - name: test_timeout
        dtype: bool
    - name: test_output
      dtype: string
  splits:
  - name: train.raw
    num_bytes: 1436124109
    num_examples: 6055
  download_size: 301095609
  dataset_size: 1436124109
configs:
- config_name: default
  data_files:
  - split: train.raw
    path: data/train.raw-*
---
