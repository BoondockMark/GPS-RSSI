'use strict';
// Deliberately fictitious terrain: never fetched from or written to a service cache.
globalThis.TrainingTerrain=(()=>{
  function samples(points,mode,station){
    return points.map(p=>{
      const inside=RouteAnalysis.located(p)&&RouteAnalysis.distance(p,station)<=25000;
      const ridge=160*Math.exp(-(((p.lat-station.lat-.018)/.003)**2));
      return {lat:p.lat,lon:p.lon,elevation:!inside||mode==='missing'?null:100+(mode==='ridge'?ridge:0),synthetic:true,
        metadata:{source:'Synthetic training terrain',resolution_basis:'Analytic fixture; no geographic grid',resolution_m:null}};
    });
  }
  return {samples};
})();
