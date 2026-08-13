import { serve } from 'https://deno.land/std@0.177.0/http/server.ts'
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

console.log("Hello from gps-ingest Edge Function!")

// Create a persistent Supabase client
const supabaseUrl = Deno.env.get('SUPABASE_URL')!
const supabaseServiceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
const supabase = createClient(supabaseUrl, supabaseServiceKey)

// Setup realtime listener in the background
const channel = supabase.channel('realtime:gps_realtime')

channel
  .on('broadcast', { event: 'GPS' }, async ({ payload }) => {
    console.log('Received GPS broadcast:', payload)
    const inner = payload?.payload || payload
    const { error } = await supabase.from('gps_realtime').insert([{
      lat: inner.lat,
      lon: inner.lon,
      speed: inner.speed,
      event: 'GPS'
    }])
    if (error) console.error("Error inserting GPS:", error)
  })
  .on('broadcast', { event: 'POWER_ON' }, async () => {
    console.log('Received POWER_ON broadcast')
    const { error } = await supabase.from('gps_realtime').insert([{ event: 'POWER_ON' }])
    if (error) console.error("Error inserting POWER_ON:", error)
  })
  .on('broadcast', { event: 'POWER_OFF' }, async () => {
    console.log('Received POWER_OFF broadcast')
    const { error } = await supabase.from('gps_realtime').insert([{ event: 'POWER_OFF' }])
    if (error) console.error("Error inserting POWER_OFF:", error)
  })
  .subscribe((status) => {
    console.log("Realtime subscription status:", status)
  })

// The edge function must respond to HTTP requests to stay alive/be invoked
serve(async (req) => {
  return new Response(
    JSON.stringify({ status: "Listening to GPS broadcasts via Realtime WebSocket" }),
    { headers: { "Content-Type": "application/json" } },
  )
})
