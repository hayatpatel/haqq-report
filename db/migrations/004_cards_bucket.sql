-- Optional: the bot creates this bucket by itself. Run this only if card upload reports "bucket not found".
insert into storage.buckets (id, name, public) values ('cards', 'cards', true)
  on conflict (id) do update set public = true;
