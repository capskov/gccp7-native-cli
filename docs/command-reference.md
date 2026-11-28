# CLI 参数参考

由本版本 CLI 的 --help 生成。业务副作用、搭配及故障恢复见 [命令地图](agent-commands.md)。

## gccp doctor

~~~text
usage: - doctor [-h]

options:
  -h, --help  show this help message and exit
~~~

## gccp bind

~~~text
usage: - bind [-h] --state STATE --runtime-root RUNTIME_ROOT --workcopy
              WORKCOPY --expected-sha256 EXPECTED_SHA256
              [--refresh-from-inspector REFRESH_FROM_INSPECTOR]
              [--accept-price-updates] [--price-summary]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --workcopy WORKCOPY
  --expected-sha256 EXPECTED_SHA256
  --refresh-from-inspector REFRESH_FROM_INSPECTOR
                        Refresh the same open project after a disk replacement
                        with unchanged verified budget
  --accept-price-updates
                        With --refresh-from-inspector, accept operator-
                        confirmed price-only changes
  --price-summary       Bind from the native resource summary
~~~

## gccp catalog

~~~text
usage: - catalog [-h] {inspect,search,apply-review,vendor-index} ...

positional arguments:
  {inspect,search,apply-review,vendor-index}
    vendor-index        Read or export the Beijing 2025 subitem index

options:
  -h, --help            show this help message and exit
~~~

## gccp quota-candidates

~~~text
usage: - quota-candidates [-h] --state STATE --runtime-root RUNTIME_ROOT
                          --project-id PROJECT_ID
                          (--code CODE | --vendor-id VENDOR_ID | --entry-id ENTRY_ID | --query-status | --close-query)
                          [--db DB]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --code CODE
  --vendor-id VENDOR_ID
                        Use an ID from the bundled Beijing 2025 export
  --entry-id ENTRY_ID   Use a reviewed SQLite entry as the lookup starting
                        point
  --query-status        Inspect the current native query window without
                        writing
  --close-query         Close one verified native query window and verify the
                        budget
  --db DB
~~~

## gccp native-inspect

~~~text
usage: - native-inspect [-h] --runtime RUNTIME

options:
  -h, --help         show this help message and exit
  --runtime RUNTIME
~~~

## gccp price-catalog

~~~text
usage: - price-catalog [-h] [--package PACKAGE] [--period PERIOD]
                       [--cache-root CACHE_ROOT]
                       [--copy-packages COPY_PACKAGES] [--text TEXT]
                       [--association ASSOCIATION] [--market MARKET]
                       [--assistant-library ASSISTANT_LIBRARY]

options:
  -h, --help            show this help message and exit
  --package PACKAGE
  --period PERIOD       Read both original local packages for YYYY-MM
  --cache-root CACHE_ROOT
  --copy-packages COPY_PACKAGES
                        Snapshot both original package folders to a new folder
  --text TEXT           Filter by material name or specification
  --association ASSOCIATION
  --market MARKET
  --assistant-library ASSISTANT_LIBRARY
                        Create a merged copy readable by the existing
                        assistant
~~~

## gccp price-plan

~~~text
usage: - price-plan [-h] --capture CAPTURE [--package PACKAGE]
                    [--period PERIOD] [--cache-root CACHE_ROOT]
                    [--association ASSOCIATION] [--market MARKET]
                    [--aliases ALIASES] --tax-basis {ex_tax,incl_tax}

options:
  -h, --help            show this help message and exit
  --capture CAPTURE
  --package PACKAGE
  --period PERIOD       Read both original local packages for YYYY-MM
  --cache-root CACHE_ROOT
  --association ASSOCIATION
                        Reviewed rows from an official association issue
  --market MARKET       Read the existing pricing assistant database
  --aliases ALIASES     Reviewed same-unit alternatives and preferred brands
  --tax-basis {ex_tax,incl_tax}
~~~

## gccp price-view

~~~text
usage: - price-view [-h] [--view {预算书,人材机汇总,controls}] --state STATE
                    --runtime-root RUNTIME_ROOT --project-id PROJECT_ID

options:
  -h, --help            show this help message and exit
  --view {预算书,人材机汇总,controls}
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
~~~

## gccp price-apply

~~~text
usage: - price-apply [-h] --command-id COMMAND_ID --request REQUEST
                     [--assistant-root ASSISTANT_ROOT]
                     [--check-only | --select-only] --state STATE
                     --runtime-root RUNTIME_ROOT --project-id PROJECT_ID

options:
  -h, --help            show this help message and exit
  --command-id COMMAND_ID
  --request REQUEST
  --assistant-root ASSISTANT_ROOT
                        Existing Guangcai quotation adapter checkout
  --check-only
  --select-only         Select one reviewed resource for the pricing assistant
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
~~~

## gccp price-reconcile

~~~text
usage: - price-reconcile [-h] --state STATE --command-id COMMAND_ID
                         [--refresh-view | --confirm-staged]

options:
  -h, --help            show this help message and exit
  --state STATE
  --command-id COMMAND_ID
  --refresh-view
  --confirm-staged
~~~

## gccp quota-reuse-plan

~~~text
usage: - quota-reuse-plan [-h] --capture CAPTURE --request REQUEST

options:
  -h, --help         show this help message and exit
  --capture CAPTURE  JSON output of native-inspect
  --request REQUEST  JSON quota and material variants to review
~~~

## gccp quota-clone

~~~text
usage: - quota-clone [-h] --state STATE --runtime-root RUNTIME_ROOT
                     --inspector INSPECTOR --project-id PROJECT_ID
                     --command-id COMMAND_ID --code CODE --quota-name
                     QUOTA_NAME --unit UNIT --material-code MATERIAL_CODE
                     [--material-name MATERIAL_NAME] [--quantity QUANTITY]
                     [--variants VARIANTS] --parent-section PARENT_SECTION

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --inspector INSPECTOR
  --project-id PROJECT_ID
  --command-id COMMAND_ID
  --code CODE
  --quota-name QUOTA_NAME
  --unit UNIT
  --material-code MATERIAL_CODE
  --material-name MATERIAL_NAME
  --quantity QUANTITY
  --variants VARIANTS   JSON array of material_name and quantity pairs, copied
                        from one source
  --parent-section PARENT_SECTION
~~~

## gccp quota-clone-reconcile

~~~text
usage: - quota-clone-reconcile [-h] --state STATE --command-id COMMAND_ID

options:
  -h, --help            show this help message and exit
  --state STATE
  --command-id COMMAND_ID
~~~

## gccp quota-add

~~~text
usage: - quota-add [-h] [--db DB] --runtime RUNTIME --request REQUEST --state
                   STATE

options:
  -h, --help         show this help message and exit
  --db DB
  --runtime RUNTIME
  --request REQUEST
  --state STATE
~~~

## gccp command-reconcile

~~~text
usage: - command-reconcile [-h] [--db DB] --runtime RUNTIME --request REQUEST
                           --state STATE [--inspector INSPECTOR]
                           [--review-expression REVIEW_EXPRESSION]

options:
  -h, --help            show this help message and exit
  --db DB
  --runtime RUNTIME
  --request REQUEST
  --state STATE
  --inspector INSPECTOR
                        Bound read-only inspector for an initial inspection
                        timeout
  --review-expression REVIEW_EXPRESSION
                        Reconcile an already corrected scaled area expression
                        against the original dispatch
~~~

## gccp runtime-prepare

~~~text
usage: - runtime-prepare [-h] [--db DB] --state STATE --inspector INSPECTOR
                         --runtime-root RUNTIME_ROOT --project-id PROJECT_ID
                         --quantity QUANTITY --command-id COMMAND_ID
                         (--parent-id PARENT_ID | --parent-section PARENT_SECTION)
                         [--selected-anchor] [--navigate-anchor]
                         [--entry-id ENTRY_ID] [--vendor-id VENDOR_ID]
                         [--library-root LIBRARY_ROOT]
                         [--expect-unit EXPECT_UNIT] [--item-label ITEM_LABEL]
                         [--candidate-code CANDIDATE_CODE]
                         [--candidate-unit CANDIDATE_UNIT]
                         [--candidate-name CANDIDATE_NAME]
                         [--candidate-specialty CANDIDATE_SPECIALTY]
                         [--save-timeout SAVE_TIMEOUT] [--no-save]

options:
  -h, --help            show this help message and exit
  --db DB
  --state STATE
  --inspector INSPECTOR
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --quantity QUANTITY
  --command-id COMMAND_ID
  --parent-id PARENT_ID
                        Business ID of an existing top-level section
  --parent-section PARENT_SECTION
                        Exact section name, repeat from the top level to the
                        target section
  --selected-anchor     Require the already-selected final quota in a section
                        path
  --navigate-anchor     With --selected-anchor, allow bounded verified native
                        grid steps within the target top-level section
  --entry-id ENTRY_ID
  --vendor-id VENDOR_ID
                        Exact subitem ID from catalog search
  --library-root LIBRARY_ROOT
                        Use this installed library instead of the bundled
                        export
  --expect-unit EXPECT_UNIT
                        With --vendor-id, require this exact live unit before
                        insertion
  --item-label ITEM_LABEL
                        Distinct work scope written to the remark; the same
                        scope cannot be inserted twice
  --candidate-code CANDIDATE_CODE
                        Exact GCCP code for catalog-free execution
  --candidate-unit CANDIDATE_UNIT
                        Exact GCCP unit for catalog-free execution
  --candidate-name CANDIDATE_NAME
                        Exact name from the GCCP candidate table
  --candidate-specialty CANDIDATE_SPECIALTY
                        Exact specialty from the same candidate row
  --save-timeout SAVE_TIMEOUT
                        Seconds to wait for a stable saved file, from 30 to
                        1800 (default: 300)
  --no-save             Apply and read back without dispatching Save; implied
                        by --parent-section
~~~

## gccp quota-run

~~~text
usage: - quota-run [-h] [--db DB] --state STATE --inspector INSPECTOR
                   --runtime-root RUNTIME_ROOT --project-id PROJECT_ID
                   --quantity QUANTITY --command-id COMMAND_ID
                   (--parent-id PARENT_ID | --parent-section PARENT_SECTION)
                   [--selected-anchor] [--navigate-anchor]
                   [--entry-id ENTRY_ID] [--vendor-id VENDOR_ID]
                   [--library-root LIBRARY_ROOT] [--expect-unit EXPECT_UNIT]
                   [--item-label ITEM_LABEL] [--candidate-code CANDIDATE_CODE]
                   [--candidate-unit CANDIDATE_UNIT]
                   [--candidate-name CANDIDATE_NAME]
                   [--candidate-specialty CANDIDATE_SPECIALTY]
                   [--save-timeout SAVE_TIMEOUT] [--no-save]

options:
  -h, --help            show this help message and exit
  --db DB
  --state STATE
  --inspector INSPECTOR
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --quantity QUANTITY
  --command-id COMMAND_ID
  --parent-id PARENT_ID
                        Business ID of an existing top-level section
  --parent-section PARENT_SECTION
                        Exact section name, repeat from the top level to the
                        target section
  --selected-anchor     Require the already-selected final quota in a section
                        path
  --navigate-anchor     With --selected-anchor, allow bounded verified native
                        grid steps within the target top-level section
  --entry-id ENTRY_ID
  --vendor-id VENDOR_ID
                        Exact subitem ID from catalog search
  --library-root LIBRARY_ROOT
                        Use this installed library instead of the bundled
                        export
  --expect-unit EXPECT_UNIT
                        With --vendor-id, require this exact live unit before
                        insertion
  --item-label ITEM_LABEL
                        Distinct work scope written to the remark; the same
                        scope cannot be inserted twice
  --candidate-code CANDIDATE_CODE
                        Exact GCCP code for catalog-free execution
  --candidate-unit CANDIDATE_UNIT
                        Exact GCCP unit for catalog-free execution
  --candidate-name CANDIDATE_NAME
                        Exact name from the GCCP candidate table
  --candidate-specialty CANDIDATE_SPECIALTY
                        Exact specialty from the same candidate row
  --save-timeout SAVE_TIMEOUT
                        Seconds to wait for a stable saved file, from 30 to
                        1800 (default: 300)
  --no-save             Apply and read back without dispatching Save; implied
                        by --parent-section
~~~

## gccp quota-supplement

~~~text
usage: - quota-supplement [-h] --state STATE --runtime-root RUNTIME_ROOT
                          --inspector INSPECTOR --project-id PROJECT_ID --code
                          CODE --name NAME --quantity QUANTITY --command-id
                          COMMAND_ID --unit {m,m2,m3,段,项,套,组,个,处,道}
                          --parent-section PARENT_SECTION [--navigate-anchor]
                          [--check-only]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --inspector INSPECTOR
  --project-id PROJECT_ID
  --code CODE
  --name NAME
  --quantity QUANTITY
  --command-id COMMAND_ID
  --unit {m,m2,m3,段,项,套,组,个,处,道}
  --parent-section PARENT_SECTION
  --navigate-anchor
  --check-only          Open the original form, check fields and cancel
                        without changing the budget
~~~

## gccp material-edit

~~~text
usage: - material-edit [-h] --state STATE --runtime-root RUNTIME_ROOT
                       --project-id PROJECT_ID --command-id COMMAND_ID --path
                       PATH --quota-code QUOTA_CODE --quota-quantity
                       QUOTA_QUANTITY --quota-note QUOTA_NOTE --material-code
                       MATERIAL_CODE --expected EXPECTED --value VALUE --field
                       {name,content,quota-name,quota-note,concrete-grade}
                       [--quota-name QUOTA_NAME] [--quota-unit QUOTA_UNIT]
                       [--resource-content] [--select-quota SELECT_QUOTA]
                       [--check-only]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --command-id COMMAND_ID
  --path PATH
  --quota-code QUOTA_CODE
  --quota-quantity QUOTA_QUANTITY
  --quota-note QUOTA_NOTE
  --material-code MATERIAL_CODE
  --expected EXPECTED
  --value VALUE
  --field {name,content,quota-name,quota-note,concrete-grade}
  --quota-name QUOTA_NAME
                        Also match the full parent quota name
  --quota-unit QUOTA_UNIT
                        Also match the parent quota unit
  --resource-content    Edit the selected quota resource table, using its
                        resource row path
  --select-quota SELECT_QUOTA
                        Select this verified parent quota path before a
                        resource edit
  --check-only
~~~

## gccp resource-edit-reconcile

~~~text
usage: - resource-edit-reconcile [-h] --state STATE --command-id COMMAND_ID
                                 [--inspector INSPECTOR]

options:
  -h, --help            show this help message and exit
  --state STATE
  --command-id COMMAND_ID
  --inspector INSPECTOR
~~~

## gccp quota-quantity

~~~text
usage: - quota-quantity [-h] --state STATE --runtime-root RUNTIME_ROOT
                        --project-id PROJECT_ID --command-id COMMAND_ID --path
                        PATH --quota-code QUOTA_CODE --quota-name QUOTA_NAME
                        --quota-unit QUOTA_UNIT --quota-note QUOTA_NOTE
                        --expected EXPECTED --value VALUE [--check-only]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --command-id COMMAND_ID
  --path PATH
  --quota-code QUOTA_CODE
  --quota-name QUOTA_NAME
  --quota-unit QUOTA_UNIT
  --quota-note QUOTA_NOTE
  --expected EXPECTED
  --value VALUE
  --check-only
~~~

## gccp quota-quantity-cancel

~~~text
usage: - quota-quantity-cancel [-h] --state STATE --command-id COMMAND_ID

options:
  -h, --help            show this help message and exit
  --state STATE
  --command-id COMMAND_ID
~~~

## gccp section-rename

~~~text
usage: - section-rename [-h] --state STATE --runtime-root RUNTIME_ROOT
                        --project-id PROJECT_ID --command-id COMMAND_ID --path
                        PATH --expected EXPECTED --value VALUE [--check-only]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --command-id COMMAND_ID
  --path PATH
  --expected EXPECTED
  --value VALUE
  --check-only
~~~

## gccp project-rename

~~~text
usage: - project-rename [-h] --state STATE --runtime-root RUNTIME_ROOT
                        --project-id PROJECT_ID --command-id COMMAND_ID
                        --expected EXPECTED --value VALUE [--check-only]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --command-id COMMAND_ID
  --expected EXPECTED
  --value VALUE
  --check-only
~~~

## gccp unit-rename

~~~text
usage: - unit-rename [-h] --state STATE --runtime-root RUNTIME_ROOT
                     --project-id PROJECT_ID --command-id COMMAND_ID
                     --project-name PROJECT_NAME --expected EXPECTED --value
                     VALUE [--check-only]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --command-id COMMAND_ID
  --project-name PROJECT_NAME
  --expected EXPECTED
  --value VALUE
  --check-only
~~~

## gccp unit-select

~~~text
usage: - unit-select [-h] --state STATE --runtime-root RUNTIME_ROOT
                     --project-id PROJECT_ID --project-name PROJECT_NAME
                     --unit-name UNIT_NAME --expected-section EXPECTED_SECTION
                     [--reviewed-file-sha256 REVIEWED_FILE_SHA256]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --project-name PROJECT_NAME
  --unit-name UNIT_NAME
  --expected-section EXPECTED_SECTION
  --reviewed-file-sha256 REVIEWED_FILE_SHA256
                        Restore a read-only unit view after a reviewed file
                        replacement
~~~

## gccp project-save

~~~text
usage: - project-save [-h] --state STATE --runtime-root RUNTIME_ROOT
                      --project-id PROJECT_ID --command-id COMMAND_ID
                      [--save-timeout SAVE_TIMEOUT] [--check-only]
                      [--price-summary]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --command-id COMMAND_ID
  --save-timeout SAVE_TIMEOUT
  --check-only
  --price-summary       Verify Save from the resource summary
~~~

## gccp quota-short-drive-factor

~~~text
usage: - quota-short-drive-factor [-h] --state STATE --runtime-root
                                  RUNTIME_ROOT --inspector INSPECTOR
                                  --project-id PROJECT_ID --quantity QUANTITY
                                  --command-id COMMAND_ID --parent-section
                                  PARENT_SECTION [--verify-only]

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --inspector INSPECTOR
  --project-id PROJECT_ID
  --quantity QUANTITY
  --command-id COMMAND_ID
  --parent-section PARENT_SECTION
  --verify-only         Verify an accepted original conversion receipt with no
                        new dispatch
~~~

## gccp quota-delete

~~~text
usage: - quota-delete [-h] --state STATE --runtime-root RUNTIME_ROOT
                      --project-id PROJECT_ID --code CODE --name NAME --unit
                      UNIT --command-id COMMAND_ID
                      (--parent-id PARENT_ID | --parent-section PARENT_SECTION)
                      [--expected-quantity EXPECTED_QUANTITY]
                      [--expected-note EXPECTED_NOTE] [--path PATH]
                      [--kind {定,换,借,补,主}] [--parent-quota PARENT_QUOTA]
                      [--navigate-target] --mode {temporary,permanent}

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --code CODE
  --name NAME
  --unit UNIT
  --command-id COMMAND_ID
  --parent-id PARENT_ID
  --parent-section PARENT_SECTION
                        Exact parent path, repeat per level. The target row
                        must already be current
  --expected-quantity EXPECTED_QUANTITY
                        Exact displayed quantity required for section-path
                        deletion
  --expected-note EXPECTED_NOTE
                        Also require this exact remark in section-path
                        deletion
  --path PATH           Also require the reviewed current row path in section-
                        path deletion
  --kind {定,换,借,补,主}    Budget row category for section-path deletion
                        (default: 定)
  --parent-quota PARENT_QUOTA
                        Full parent quota name for temporary main material
                        deletion
  --navigate-target     Select the uniquely matched budget row using native
                        navigation
  --mode {temporary,permanent}
~~~

## gccp section-delete

~~~text
usage: - section-delete [-h] --state STATE --runtime-root RUNTIME_ROOT
                        --project-id PROJECT_ID --command-id COMMAND_ID
                        --parent-section PARENT_SECTION

options:
  -h, --help            show this help message and exit
  --state STATE
  --runtime-root RUNTIME_ROOT
  --project-id PROJECT_ID
  --command-id COMMAND_ID
  --parent-section PARENT_SECTION
                        Exact section path, including the empty section to
                        remove
~~~

## gccp quota-delete-confirm

~~~text
usage: - quota-delete-confirm [-h] --state STATE --command-id COMMAND_ID
                              [--reviewed-file-sha256 REVIEWED_FILE_SHA256]

options:
  -h, --help            show this help message and exit
  --state STATE
  --command-id COMMAND_ID
  --reviewed-file-sha256 REVIEWED_FILE_SHA256
                        Operator-reviewed current disk digest for the original
                        open dialog
~~~

## gccp quota-delete-reconcile

~~~text
usage: - quota-delete-reconcile [-h] --state STATE --command-id COMMAND_ID

options:
  -h, --help            show this help message and exit
  --state STATE
  --command-id COMMAND_ID
~~~

## gccp command-status

~~~text
usage: - command-status [-h] --state STATE --command-id COMMAND_ID
                        [--include-resources]

options:
  -h, --help            show this help message and exit
  --state STATE
  --command-id COMMAND_ID
  --include-resources   Include archived resource rows verified for this
                        command
~~~

## gccp catalog search

~~~text
usage: - catalog search [-h] [--db DB] [--code CODE] [--text TEXT]
                        [--source-id SOURCE_ID] [--spec SPEC]
                        [--specialty SPECIALTY] [--limit LIMIT]
                        [--vendor-id VENDOR_ID]

options:
  -h, --help            show this help message and exit
  --db DB               Explicit SQLite input for historical workflows
  --code CODE
  --text TEXT
  --source-id SOURCE_ID
  --spec SPEC
  --specialty SPECIALTY
  --limit LIMIT
  --vendor-id VENDOR_ID
~~~

## gccp catalog inspect

~~~text
usage: - catalog inspect [-h] [--db DB]

options:
  -h, --help  show this help message and exit
  --db DB     Explicit SQLite input for historical workflows
~~~

## gccp catalog vendor-index

~~~text
usage: - catalog vendor-index [-h] [--library-root LIBRARY_ROOT]
                              [--vendor-id VENDOR_ID] [--code CODE]
                              [--text TEXT] [--specialty SPECIALTY]
                              [--output OUTPUT] [--limit LIMIT]

options:
  -h, --help            show this help message and exit
  --library-root LIBRARY_ROOT
                        Read from this installation instead of the bundled
                        export
  --vendor-id VENDOR_ID
  --code CODE
  --text TEXT
  --specialty SPECIALTY
  --output OUTPUT       Create a new UTF-8 JSON export
  --limit LIMIT         Maximum query rows when no file is exported
~~~

## gccp catalog apply-review

~~~text
usage: - catalog apply-review [-h] --db DB --review REVIEW

options:
  -h, --help       show this help message and exit
  --db DB          Explicit SQLite input for historical workflows
  --review REVIEW
~~~
