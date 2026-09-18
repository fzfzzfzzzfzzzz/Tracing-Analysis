#include <cuda/functional>
#include <cuda_runtime.h>

__global__ void increment(int* value) {
  *value += 1;
}

int main() {
  return 0;
}
