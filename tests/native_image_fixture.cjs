'use strict';
// NativeImage interface for tests, backed by genuine Chromium screenshot pixels.
// Not used by production. crop/resize are deterministic nearest-neighbor here;
// production uses Electron's nativeImage implementation.
class BitmapImage {
  constructor(width,height,rgba,png=Buffer.alloc(0)){this.width=width;this.height=height;this.rgba=Buffer.from(rgba);this.png=png;}
  static from(row){return new BitmapImage(row.width,row.height,Buffer.from(row.rgba,'base64'),Buffer.from(row.png,'base64'));}
  getSize(){return {width:this.width,height:this.height};}
  isEmpty(){return !this.width||!this.height;}
  toBitmap(){return this.rgba;}
  toPNG(){return this.png;}
  crop({x,y,width,height}){const out=Buffer.alloc(width*height*4);for(let j=0;j<height;j++)this.rgba.copy(out,j*width*4,((j+y)*this.width+x)*4,((j+y)*this.width+x+width)*4);return new BitmapImage(width,height,out);}
  resize({width,height}){if(width===this.width && height===this.height)return this;const out=Buffer.alloc(width*height*4);for(let y=0;y<height;y++)for(let x=0;x<width;x++){const i=(Math.floor(y*this.height/height)*this.width+Math.floor(x*this.width/width))*4;this.rgba.copy(out,(y*width+x)*4,i,i+4);}return new BitmapImage(width,height,out);}
}
module.exports={BitmapImage};
