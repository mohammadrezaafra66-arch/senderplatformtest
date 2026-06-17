# Phase 2 Test Report

Summary: **16/16 passed**


- ✅ **health**

- ✅ **1_valid_excel_preview**
  - status=200, valid=2

- ✅ **1_valid_excel_commit**
  - {"status": "committed", "import_batch_id": 16, "total_rows": 2, "created_contacts_count": 2, "invalid_rows_count": 0, "duplicate_rows_count": 0, "errors_count": 0, "message": "Import committed successfully"}

- ✅ **1_valid_excel_campaign**
  - {"status": "draft_created", "campaign_id": 5, "import_batch_id": 16, "contacts_attached_count": 2, "skipped_contacts_count": 0, "message": "Campaign draft created from import successfully"}

- ✅ **2_invalid_phone_preview**
  - invalid=1, valid=1

- ✅ **2_invalid_phone_commit**
  - {"status": "committed", "import_batch_id": 17, "total_rows": 2, "created_contacts_count": 1, "invalid_rows_count": 1, "duplicate_rows_count": 0, "errors_count": 1, "message": "Import committed successfully"}

- ✅ **3_missing_phone_preview**
  - missing=1

- ✅ **3_missing_phone_commit**
  - {"status": "committed", "import_batch_id": 18, "total_rows": 2, "created_contacts_count": 1, "invalid_rows_count": 1, "duplicate_rows_count": 0, "errors_count": 1, "message": "Import committed successfully"}

- ✅ **4_internal_duplicate_preview**
  - dup=1, valid=1

- ✅ **4_internal_duplicate_commit**
  - {"status": "committed", "import_batch_id": 19, "total_rows": 2, "created_contacts_count": 1, "invalid_rows_count": 0, "duplicate_rows_count": 1, "errors_count": 1, "message": "Import committed successfully"}

- ✅ **5_db_duplicate**
  - commit1={"status": "committed", "import_batch_id": 20, "total_rows": 1, "created_contacts_count": 1, "invalid_rows_count": 0, "duplicate_rows_count": 0, "errors_count": 0, "message": "Import committed successfully"} commit2={"status": "failed", "import_batch_id": 21, "total_rows": 1, "created_contacts_count": 0, "invalid_rows_count": 0, "duplicate_rows_count": 1, "errors_count": 1, "message": "No valid contacts were committed"}

- ✅ **6_missing_phone_column**
  - status=200, errors=[{'code': 'missing_required_phone_column', 'message': 'Required phone column was not found in the Excel file.'}]

- ✅ **7_invalid_extension**
  - status=400

- ✅ **8_oversized_file**
  - status=413

- ✅ **9_blacklisted**
  - {"status": "draft_created", "campaign_id": 6, "import_batch_id": 22, "contacts_attached_count": 1, "skipped_contacts_count": 1, "message": "Campaign draft created from import successfully"}

- ✅ **10_opted_out**
  - {"status": "draft_created", "campaign_id": 7, "import_batch_id": 22, "contacts_attached_count": 1, "skipped_contacts_count": 1, "message": "Campaign draft created from import successfully"}
