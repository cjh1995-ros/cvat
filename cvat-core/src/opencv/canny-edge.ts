// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import { BaseImageFilter } from './image-processing';

export interface CannyEdgeOptions {
    threshold1: number;
    threshold2: number;
    apertureSize: number;
    l2gradient: boolean;
}

export default class CannyEdgeImplementation extends BaseImageFilter {
    private cv: any;
    private threshold1 = 50;
    private threshold2 = 150;
    private apertureSize = 3;
    private l2gradient = false;

    constructor(cv: any) {
        super();
        this.cv = cv;
    }

    public configure(options: Partial<CannyEdgeOptions>): void {
        if (typeof options.threshold1 === 'number') {
            this.threshold1 = options.threshold1;
        }

        if (typeof options.threshold2 === 'number') {
            this.threshold2 = options.threshold2;
        }

        if (typeof options.apertureSize === 'number') {
            // the aperture size must be an odd integer in [3, 7]
            const size = Math.min(7, Math.max(3, Math.round(options.apertureSize)));
            this.apertureSize = size % 2 === 0 ? size + 1 : size;
        }

        if (typeof options.l2gradient === 'boolean') {
            this.l2gradient = options.l2gradient;
        }
    }

    public processImage(src: ImageData, frameNumber: number): ImageData {
        const { cv } = this;
        let matImage = null;
        const gray = new cv.Mat();
        const edges = new cv.Mat();
        const RGBADist = new cv.Mat();
        try {
            this.currentProcessedImage = frameNumber;
            matImage = cv.matFromImageData(src);
            cv.cvtColor(matImage, gray, cv.COLOR_RGBA2GRAY, 0);
            cv.Canny(gray, edges, this.threshold1, this.threshold2, this.apertureSize, this.l2gradient);
            // edges is a single-channel binary image; expand it back to RGBA for display
            cv.cvtColor(edges, RGBADist, cv.COLOR_GRAY2RGBA, 0);
            const arr = new Uint8ClampedArray(RGBADist.data, RGBADist.cols, RGBADist.rows);
            return new ImageData(arr, src.width, src.height);
        } catch (e: unknown) {
            throw e instanceof Error ? e : new Error('Unknown error');
        } finally {
            if (matImage) {
                matImage.delete();
            }

            gray.delete();
            edges.delete();
            RGBADist.delete();
        }
    }
}
