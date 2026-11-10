-- ledger: the withheld selection outcome. Owner: Inesh. Spec 0079.
--
-- withheld: today's rule would call a number, but the change context would change which one, so
-- nobody is called and a person verifies. Stored like no_provable_number: pick_has_contact_point
-- already refuses a contact point on any outcome but picked.
--
-- Its own file: a new enum value can't be used in the transaction that adds it.

alter type ledger.selection_outcome add value if not exists 'withheld';
