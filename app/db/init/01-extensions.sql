-- btree_gist lets a GiST exclusion constraint compare a plain equality column
-- (doctor_id) alongside a range column (appointment_range) in the same index.
-- This is what makes the double-booking prevention in 02-schema.sql possible
-- without any application-level locking. See ADR-005.
CREATE EXTENSION IF NOT EXISTS btree_gist;
