-- Migration: Add pin_hash column to contacts table for phone+PIN auth
-- Run: psql $DATABASE_URL -f migrations/001_add_pin_hash.sql

ALTER TABLE contacts ADD COLUMN IF NOT EXISTS pin_hash TEXT;
