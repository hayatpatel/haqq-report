-- Hard database guarantee: one post per draft per platform, even if two jobs race.
-- Safe to run more than once. If it errors with "could not create unique index", delete the older duplicate rows
-- from the posts table first (select draft_id, platform, count(*) from posts group by 1,2 having count(*) > 1).
create unique index if not exists posts_draft_platform_uq on posts (draft_id, platform);
