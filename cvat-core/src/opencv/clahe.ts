// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { BaseImageFilter } from './image-processing';

export interface CLAHEOptions {
    clipLimit: number;
    tileGridSize: number;
}

// Contrast Limited Adaptive Histogram Equalization.
// Unlike global histogram equalization, the contrast is equalized per tile, which
// avoids over-amplifying noise in otherwise uniform regions.
export default class CLAHEImplementation extends BaseImageFilter {
    private cv: any;
    private clipLimit = 2.0;
    private tileGridSize = 8;

    constructor(cv: any) {
        super();
        this.cv = cv;
    }

    public configure(options: Partial<CLAHEOptions>): void {
        if (typeof options.clipLimit === 'number') {
            this.clipLimit = options.clipLimit;
        }

        if (typeof options.tileGridSize === 'number') {
            this.tileGridSize = Math.max(1, Math.round(options.tileGridSize));
        }
    }

    public processImage(src: ImageData, frameNumber: number): ImageData {
        const { cv } = this;
        let matImage = null;
        let clahe = null;
        const RGBImage = new cv.Mat();
        const YUVImage = new cv.Mat();
        const YUVDist = new cv.Mat();
        const RGBDist = new cv.Mat();
        const RGBADist = new cv.Mat();
        let channels = new cv.MatVector();
        const equalizedY = new cv.Mat();
        try {
            this.currentProcessedImage = frameNumber;
            matImage = cv.matFromImageData(src);
            cv.cvtColor(matImage, RGBImage, cv.COLOR_RGBA2RGB, 0);
            cv.cvtColor(RGBImage, YUVImage, cv.COLOR_RGB2YUV, 0);
            cv.split(YUVImage, channels);
            const [Y, U, V] = [channels.get(0), channels.get(1), channels.get(2)];
            channels.delete();
            channels = null;
            // createCLAHE is not exposed in the bundled opencv.js build; use the constructor form
            clahe = new cv.CLAHE(this.clipLimit, new cv.Size(this.tileGridSize, this.tileGridSize));
            clahe.apply(Y, equalizedY);
            Y.delete();
            channels = new cv.MatVector();
            channels.push_back(equalizedY); equalizedY.delete();
            channels.push_back(U); U.delete();
            channels.push_back(V); V.delete();
            cv.merge(channels, YUVDist);
            cv.cvtColor(YUVDist, RGBDist, cv.COLOR_YUV2RGB, 0);
            cv.cvtColor(RGBDist, RGBADist, cv.COLOR_RGB2RGBA, 0);
            const arr = new Uint8ClampedArray(RGBADist.data, RGBADist.cols, RGBADist.rows);
            return new ImageData(arr, src.width, src.height);
        } catch (e: unknown) {
            throw e instanceof Error ? e : new Error('Unknown error');
        } finally {
            if (matImage) {
                matImage.delete();
            }

            if (channels) {
                channels.delete();
            }

            if (clahe) {
                clahe.delete();
            }

            RGBImage.delete();
            YUVImage.delete();
            YUVDist.delete();
            RGBDist.delete();
            RGBADist.delete();
        }
    }
}
