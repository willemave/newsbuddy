-- Visual demo overlay. Run after newsly-admin e2e seed --namespace appstore-launch.
-- Deliberately restricted to disposable launch databases and the seeded demo user.
\set ON_ERROR_STOP on
BEGIN;
DO $$
BEGIN
  IF current_database() NOT LIKE 'newsbuddy_launch_%' THEN
    RAISE EXCEPTION 'Use a disposable newsbuddy_launch_* database';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM contents WHERE content_metadata->>'fixture_namespace'='appstore-launch') THEN
    RAISE EXCEPTION 'Seed namespace appstore-launch first';
  END IF;
END $$;
CREATE TEMP TABLE launch_stories (position int, topic text, title text, source text, url text, intro text, context text, takeaway text) ON COMMIT DROP;
INSERT INTO launch_stories VALUES
(0,'Space','Inside a stellar nursery','NASA','https://science.nasa.gov/asset/webb/pillars-of-creation-nircam-image/',
'Webb reveals young stars taking shape in the Pillars of Creation. Its infrared view looks through dust that hides much of the scene in visible light.',
'These columns of gas and dust lie in the Eagle Nebula, about 6,500 light-years away. Dense knots of material collapse under gravity as new stars form. The observations give astronomers a detailed view of a stellar nursery.',
'Looking at different wavelengths changes what we can learn from the same patch of sky.'),
(7,'Earth','How trees cool a city','U.S. EPA','https://www.epa.gov/heatislands/benefits-trees-and-vegetation',
'Tree cover changes the feel of a street in two ways: shade keeps sunlight off surfaces, while water released through leaves helps cool the surrounding air.',
'Pavement and buildings absorb sunlight and warm up. A canopy interrupts that process. Evapotranspiration adds another cooling mechanism as water moves from soil and leaves into the air. Together, these effects help explain the role of vegetation in reducing urban heat.',
'Shade and water work together; a cooler city depends on more than a change in surface color.'),
(1,'Space','Reading the skies with Webb','NASA','https://science.nasa.gov/mission/webb/science-overview/science-explainers/how-will-webb-study-exoplanets/',
'Light from a distant world carries clues about its atmosphere. Webb uses infrared observations to help researchers identify the molecules around planets beyond our solar system.',
'When a planet passes in front of its star, some starlight travels through its atmosphere. Different molecules absorb different wavelengths, leaving patterns that astronomers can study. A spectrum turns a tiny change in light into evidence about a world we cannot visit.',
'The useful question is not simply whether a planet has an atmosphere, but what the evidence can tell us about its composition.'),
(2,'Space','Water signatures on a distant planet','NASA','https://science.nasa.gov/asset/webb/exoplanet-wasp-96-b-niriss-transmission-spectrum/',
'Webb’s observations of WASP-96 b revealed a water signature along with evidence for clouds and haze. This hot gas giant shows how much information can be hidden in a small slice of starlight.',
'A transmission spectrum compares light at different wavelengths as a planet crosses its star. The shape of the signal helps researchers work out which atmospheric explanations fit. Finding water in an atmosphere is a chemical observation; it does not establish that a planet is habitable.',
'An atmospheric ingredient is one piece of a much larger planetary picture.'),
(3,'Space','Taking the temperature of a rocky world','NASA','https://www.nasa.gov/universe/nasas-webb-measures-the-temperature-of-a-rocky-exoplanet/',
'TRAPPIST-1 b offers another way to study distant planets: measure their heat. Webb observed the mid-infrared light emitted by the rocky planet.',
'The measurement suggested that TRAPPIST-1 b lacks a substantial atmosphere. This technique adds a different kind of evidence to studies that look at starlight passing through an atmosphere. Together, these methods help researchers narrow the range of possible conditions on distant worlds.',
'Planetary science advances by combining measurements that answer different questions.'),
(4,'Earth','Why corals lose their color','NOAA','https://oceanservice.noaa.gov/facts/coral_bleach.html',
'When corals are stressed, they can lose the algae that live in their tissues and turn white. Warmer ocean temperatures are a major cause of this bleaching.',
'Bleached coral is not necessarily dead, but the loss leaves it under greater stress. The distinction matters: bleaching describes a change in the relationship between coral and algae, not an automatic statement that recovery is impossible.',
'Watch both the stress event and what happens afterward; a white reef does not tell the whole story.'),
(5,'Energy','Saving sunshine for later','U.S. Department of Energy','https://www.energy.gov/cmei/systems/solar-integration-solar-energy-and-storage-basics',
'Solar generation and electricity demand do not always arrive at the same time. Storage lets energy collected during sunny hours be used later.',
'Batteries can be paired with photovoltaic panels, while thermal storage can work with concentrating solar power. The shared idea is to separate the time energy is captured from the time it is delivered. Storage therefore changes how a solar system can serve demand beyond the moment of generation.',
'A useful solar system is about timing as well as total energy production.'),
(6,'Energy','A battery is only part of the system','U.S. Department of Energy','https://www.energy.gov/cmei/systems/articles/solar-plus-storage-101',
'A solar-plus-storage installation needs more than battery cells. Inverters and wiring also form part of the equipment that turns stored energy into usable electricity.',
'Looking at the whole system helps explain why storage projects involve choices beyond battery capacity. Components must work together to accept energy, store it, and deliver it when needed. Comparing a battery alone with a complete installation can miss that wider engineering task.',
'Capacity tells one part of the story; the surrounding equipment makes that capacity useful.');
DO $$
DECLARE s record; uid integer; cid integer; lid integer; sk text; blocks jsonb;
BEGIN
 SELECT user_id INTO STRICT uid FROM briefing_states WHERE user_id=(SELECT user_id FROM briefing_lenses WHERE key IN ('articles','e2e-appstore-launch','demo-space') ORDER BY id LIMIT 1);
 DELETE FROM briefing_segments WHERE user_id=uid;
 DELETE FROM briefing_lenses WHERE user_id=uid;
 FOR s IN SELECT DISTINCT topic FROM launch_stories LOOP
   INSERT INTO briefing_lenses(user_id,key,tier,title,deck,position,status,centroid_weight,created_at,updated_at)
   VALUES(uid,'demo-'||lower(s.topic),'longform',s.topic,'Ideas worth understanding.',CASE s.topic WHEN 'Space' THEN 10 WHEN 'Earth' THEN 20 ELSE 30 END,'active',0,now(),now());
 END LOOP;
 FOR s IN SELECT * FROM launch_stories ORDER BY position LOOP
   INSERT INTO contents(content_type,url,source_url,title,source,status,retry_count,classification,content_metadata,created_at,updated_at,processed_at,platform,is_aggregate,search_text)
   VALUES('article',s.url,s.url,s.title,s.source,'completed',0,'to_read',jsonb_build_object('fixture_namespace','appstore-launch','summary_kind','long_structured','summary_version',1,'summary',jsonb_build_object('title',s.title,'overview',s.intro||E'\n\n'||s.context,'one_line',s.intro,'bullet_points',jsonb_build_array(jsonb_build_object('text',s.takeaway,'category',lower(s.topic))),'topics',jsonb_build_array(s.topic),'classification','to_read','full_markdown',s.intro||E'\n\n'||s.context||E'\n\n'||s.takeaway)),now(),now(),now(),'web',false,s.title)
   ON CONFLICT(url,content_type) DO UPDATE SET title=EXCLUDED.title,content_metadata=(contents.content_metadata::jsonb || EXCLUDED.content_metadata::jsonb)::json RETURNING id INTO cid;
   INSERT INTO content_status(user_id,content_id,status,created_at,updated_at) VALUES(uid,cid,'inbox',now(),now()) ON CONFLICT(user_id,content_id) DO UPDATE SET status='inbox';
   SELECT id INTO lid FROM briefing_lenses WHERE user_id=uid AND key='demo-'||lower(s.topic);
   sk := 'content:'||cid;
   blocks := jsonb_build_array(jsonb_build_object('type','passage','weight','lead','paragraphs',jsonb_build_array(jsonb_build_object('runs',jsonb_build_array(jsonb_build_object('kind','source_link','text',s.title,'source_key',sk),jsonb_build_object('kind','text','text',E'\n'||s.intro))))),jsonb_build_object('type','passage','paragraphs',jsonb_build_array(jsonb_build_object('runs',jsonb_build_array(jsonb_build_object('kind','text','text',s.context))),jsonb_build_object('runs',jsonb_build_array(jsonb_build_object('kind','text','text',s.takeaway))))));
   INSERT INTO briefing_segments(lens_id,user_id,blocks,markdown_raw,narration_text,source_keys,status,model,prompt_version,warnings,created_at,updated_at,event_groups)
   VALUES(lid,uid,blocks,s.title||E'\n\n'||s.intro||E'\n\n'||s.context||E'\n\n'||s.takeaway,s.intro||' '||s.context||' '||s.takeaway,jsonb_build_array(sk),'active','editorial-demo','visual-demo-v1','[]',now()-s.position*interval '1 minute',now(),'[]');
 END LOOP;
 -- Retain the existing source-linked NASA image as the lead of the Space edition.
 SELECT id INTO cid FROM contents WHERE content_metadata->>'fixture_namespace'='appstore-launch' AND source='NASA' AND content_metadata::jsonb ? 'image_url' ORDER BY id LIMIT 1;
 IF cid IS NOT NULL THEN
   UPDATE briefing_segments SET blocks=jsonb_insert(briefing_segments.blocks,'{1}',jsonb_build_object('type','figure','image_url',(SELECT content_metadata->>'image_url' FROM contents WHERE id=cid),'caption','Pillars of Creation · NASA, ESA, CSA, STScI','placement','full')) WHERE id=(SELECT id FROM briefing_segments WHERE user_id=uid ORDER BY created_at DESC LIMIT 1);
 END IF;
 DELETE FROM content_read_status WHERE user_id=uid;
 UPDATE briefing_states SET version=version+1,masthead_deck='Space, our planet, and the energy that connects them.',last_append_at=now() WHERE user_id=uid;
END $$;
COMMIT;
